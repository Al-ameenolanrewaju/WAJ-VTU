const { default: makeWASocket, DisconnectReason, BufferJSON, initAuthCreds } = require('@whiskeysockets/baileys');
const { createClient } = require('@supabase/supabase-js');
const { Boom } = require('@hapi/boom');
const qrcode = require('qrcode-terminal');
const qrImage = require('qrcode');
const axios = require('axios');
const http = require('http');
const NodeCache = require('node-cache');

// Retry counter cache for Baileys E2EE decryption
const msgRetryCounterCache = new NodeCache();

function normalizeWebhookUrl(value) {
    const configuredUrl = value || 'http://localhost:5000/webhook';
    try {
        const url = new URL(configuredUrl);
        if (url.pathname === '/whatsapp/webhook' || url.pathname === '/whatsapp/webhook/') {
            url.pathname = '/webhook';
        }
        return url.toString().replace(/\/$/, '');
    } catch (error) {
        console.error(`Invalid FLASK_WEBHOOK_URL: ${configuredUrl}`);
        return 'http://localhost:5000/webhook';
    }
}

const FLASK_WEBHOOK_URL = normalizeWebhookUrl(process.env.FLASK_WEBHOOK_URL);
const BRIDGE_API_TOKEN = process.env.BRIDGE_API_TOKEN || '';
const PORT = Number(process.env.PORT || 3000);
const PAIRING_PHONE_NUMBER = process.env.PAIRING_PHONE_NUMBER || '';

let reconnectTimer = null;
let reconnectAttempt = 0;
let startingBot = false;

// Environment variable sanitization
let rawSupabaseUrl = (process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL || '').trim().replace(/^["']|["']$/g, '');
const SUPABASE_SERVICE_ROLE_KEY = (process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_KEY || process.env.SUPABASE_ANON_KEY || '').trim().replace(/^["']|["']$/g, '');

if (rawSupabaseUrl && !rawSupabaseUrl.startsWith('http://') && !rawSupabaseUrl.startsWith('https://')) {
    rawSupabaseUrl = `https://${rawSupabaseUrl}`;
}

const SUPABASE_URL = rawSupabaseUrl;

if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY) {
    console.error('❌ ERROR: Missing Supabase environment variables!');
    process.exit(1);
}

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

let sock;
let whatsappConnected = false;
let latestQrDataUrl = null;
let resettingSession = false;

// Safely extracts text content across plain text, ephemeral wrappers, interactive buttons, and list replies
function extractMessageContent(message) {
    if (!message) return '';

    // Step 1: Unwrap container layers
    let content = message.ephemeralMessage?.message ||
                  message.viewOnceMessage?.message ||
                  message.viewOnceMessageV2?.message ||
                  message;

    // Step 2: Extract text from standard, button, list, and interactive responses
    return (
        content.conversation ||
        content.extendedTextMessage?.text ||
        content.buttonsResponseMessage?.selectedButtonId ||
        content.listResponseMessage?.singleSelectReply?.selectedRowId ||
        content.templateButtonReplyMessage?.selectedId ||
        content.interactiveResponseMessage?.nativeFlowResponseMessage?.paramsJson ||
        ''
    ).trim();
}

// Supabase session handler
async function useSupabaseAuthState(sessionId = 'main_session') {
    const readData = async (type, id) => {
        const key = `${type}-${id}`;
        const { data } = await supabase
            .from('whatsapp_sessions')
            .select('data')
            .eq('id', `${sessionId}_${key}`)
            .maybeSingle();

        if (data?.data) {
            return JSON.parse(JSON.stringify(data.data), BufferJSON.reviver);
        }
        return null;
    };

    const writeData = async (type, id, val) => {
        const key = `${type}-${id}`;
        if (!val) {
            await supabase
                .from('whatsapp_sessions')
                .delete()
                .eq('id', `${sessionId}_${key}`);
            return;
        }

        const valueJSON = JSON.parse(JSON.stringify(val, BufferJSON.replacer));
        await supabase.from('whatsapp_sessions').upsert({
            id: `${sessionId}_${key}`,
            data: valueJSON,
            updated_at: new Date().toISOString()
        });
    };

    let creds = await readData('creds', 'main');
    if (!creds) {
        creds = initAuthCreds();
    }

    return {
        state: {
            creds,
            keys: {
                get: async (type, ids) => {
                    const data = {};
                    await Promise.all(
                        ids.map(async (id) => {
                            const value = await readData(type, id);
                            if (value) data[id] = value;
                        })
                    );
                    return data;
                },
                set: async (data) => {
                    const tasks = [];
                    for (const category in data) {
                        for (const id in data[category]) {
                            const value = data[category][id];
                            tasks.push(writeData(category, id, value));
                        }
                    }
                    await Promise.all(tasks);
                }
            }
        },
        saveCreds: async () => {
            await writeData('creds', 'main', creds);
        }
    };
}

function sendJson(response, statusCode, body) {
    response.writeHead(statusCode, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify(body));
}

async function resetWhatsAppSession() {
    resettingSession = true;
    whatsappConnected = false;
    latestQrDataUrl = null;
    reconnectAttempt = 0;

    if (sock) {
        try {
            sock.end(new Error('WhatsApp session reset requested'));
        } catch (error) {
            console.warn('Unable to close socket:', error.message);
        }
        sock = null;
    }

    const { error } = await supabase
        .from('whatsapp_sessions')
        .delete()
        .like('id', 'main_session_%');

    if (error) {
        resettingSession = false;
        throw error;
    }

    startingBot = false;
    resettingSession = false;
    await startBot();
}

function sendQrPage(response) {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end(`<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WhatsApp Pairing QR</title>
<style>
  body{font-family:Arial,sans-serif;text-align:center;padding:24px;background-color:#f9f9f9}
  .card{background:#fff;padding:20px;border-radius:12px;box-shadow:0 2px 10px rgba(0,0,0,0.1);display:inline-block}
  img{width:min(80vw,360px);height:auto;border-radius:8px}
  p{color:#555;font-size:16px}
</style>
</head><body>
<div class="card">
  <h1>WhatsApp Pairing</h1>
  <p id="status">Waiting for QR code generation...</p>
  <img id="qr" alt="WhatsApp pairing QR code" hidden>
</div>
<script>
async function refreshQr(){
  const response = await fetch('/qr/image');
  const image = document.getElementById('qr');
  const status = document.getElementById('status');
  if(response.ok){
    image.src = URL.createObjectURL(await response.blob());
    image.hidden = false;
    status.textContent = 'Scan this QR code with WhatsApp Linked Devices';
  } else {
    image.hidden = true;
    status.textContent = response.status === 409 ? 'WhatsApp is already connected!' : 'Waiting for connection...';
  }
}
refreshQr();
setInterval(refreshQr, 4000);
</script></body></html>`);
}

function startHttpServer() {
    const server = http.createServer(async (request, response) => {
        const url = new URL(request.url, `http://${request.headers.host || 'localhost'}`);

        if ((request.method === 'GET' || request.method === 'HEAD') && (url.pathname === '/health' || url.pathname === '/')) {
            response.writeHead(200, { 'Content-Type': 'text/plain', 'Cache-Control': 'no-cache' });
            return response.end('OK');
        }

        if (request.method === 'GET' && (url.pathname === '/qr' || url.pathname === '/qr/image')) {
            if (!latestQrDataUrl) return sendJson(response, 409, { error: 'QR code not available or already connected' });
            if (url.pathname === '/qr/image') {
                const image = Buffer.from(latestQrDataUrl.split(',')[1], 'base64');
                response.writeHead(200, { 'Content-Type': 'image/png', 'Cache-Control': 'no-store' });
                return response.end(image);
            }
            return sendQrPage(response);
        }

        if (request.method === 'POST' && url.pathname === '/api/resetSession') {
            if (!BRIDGE_API_TOKEN || request.headers.authorization !== `Bearer ${BRIDGE_API_TOKEN}`) {
                return sendJson(response, 401, { error: 'Unauthorized' });
            }
            try {
                await resetWhatsAppSession();
                return sendJson(response, 200, { status: 'session_reset', message: 'Scan the new QR code at /qr' });
            } catch (error) {
                return sendJson(response, 500, { error: 'Unable to reset WhatsApp session' });
            }
        }

        if (request.method !== 'POST' || url.pathname !== '/api/sendText') {
            return sendJson(response, 404, { error: 'Not found' });
        }
        if (BRIDGE_API_TOKEN && request.headers.authorization !== `Bearer ${BRIDGE_API_TOKEN}`) {
            return sendJson(response, 401, { error: 'Unauthorized' });
        }

        let rawBody = '';
        request.on('data', (chunk) => { rawBody += chunk; });
        request.on('end', async () => {
            try {
                const { chatId, text } = JSON.parse(rawBody || '{}');
                if (!chatId || !text) {
                    return sendJson(response, 400, { error: 'chatId and text are required' });
                }
                if (!sock || !whatsappConnected) {
                    return sendJson(response, 503, { error: 'WhatsApp is not connected' });
                }

                await sock.sendMessage(chatId, { text });
                return sendJson(response, 200, { status: 'sent' });
            } catch (error) {
                console.error('Error sending message:', error.message);
                return sendJson(response, 500, { error: 'Failed to send message' });
            }
        });
    });

    server.listen(PORT, '0.0.0.0', () => {
        console.log(`WhatsApp bridge HTTP server listening on port ${PORT}`);
    });
}

async function startBot() {
    if (startingBot) return;
    startingBot = true;

    const { state, saveCreds } = await useSupabaseAuthState('main_session');

    sock = makeWASocket({
        auth: state,
        msgRetryCounterCache,
        printQRInTerminal: false,
        connectTimeoutMs: 60000,
        defaultQueryTimeoutMs: 60000,
        keepAliveIntervalMs: 25000,
        retryRequestDelayMs: 5000,
        markOnlineOnConnect: false,
        syncFullHistory: false
    });

    sock.ev.on('creds.update', saveCreds);

    if (PAIRING_PHONE_NUMBER && !sock.authState.creds.registered) {
        setTimeout(async () => {
            try {
                const pairingCode = await sock.requestPairingCode(PAIRING_PHONE_NUMBER.replace(/[^0-9]/g, ''));
                console.log(`\n=============================================\nPAIRING CODE: ${pairingCode}\n=============================================\n`);
            } catch (err) {
                console.error('Error generating pairing code:', err.message);
            }
        }, 4000);
    }

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        if (qr) {
            qrcode.generate(qr, { small: true });
            qrImage.toDataURL(qr, { margin: 2, width: 600 })
                .then((dataUrl) => { latestQrDataUrl = dataUrl; })
                .catch((error) => console.error('Unable to render QR:', error.message));
        }
        if (connection === 'close') {
            whatsappConnected = false;
            const shouldReconnect = !resettingSession && ((lastDisconnect?.error instanceof Boom)
                ? lastDisconnect.error.output?.statusCode !== DisconnectReason.loggedOut
                : true);
            startingBot = false;
            if (shouldReconnect && !reconnectTimer) {
                const delay = Math.min(60000, 5000 * (2 ** reconnectAttempt));
                reconnectAttempt += 1;
                console.log(`WhatsApp disconnected. Reconnecting in ${delay}ms...`);
                reconnectTimer = setTimeout(() => {
                    reconnectTimer = null;
                    startBot().catch((error) => {
                        startingBot = false;
                        console.error('Unable to restart WhatsApp bridge:', error.message);
                    });
                }, delay);
            }
        } else if (connection === 'open') {
            whatsappConnected = true;
            startingBot = false;
            reconnectAttempt = 0;
            latestQrDataUrl = null;
            console.log('✅ WhatsApp Bridge Connected Successfully!');
        }
    });

    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        if (type !== 'notify') return;

        for (const msg of messages) {
            // Filter invalid, system, group, status broadcasts, or self-sent messages
            if (!msg || !msg.message || msg.key.fromMe) continue;
            const sender = msg.key.remoteJid;
            if (!sender || sender.endsWith('@g.us') || sender.endsWith('@broadcast')) continue;

            const text = extractMessageContent(msg.message);
            console.log(`📩 Incoming from ${sender}: "${text}"`);

            if (!text) continue;

            try {
                const response = await axios.post(
                    FLASK_WEBHOOK_URL,
                    {
                        from: sender,
                        body: text,
                        fromMe: false,
                        messageId: msg.key.id,
                        pushName: msg.pushName || '',
                        timestamp: msg.messageTimestamp
                    },
                    {
                        headers: BRIDGE_API_TOKEN ? { Authorization: `Bearer ${BRIDGE_API_TOKEN}` } : {},
                        timeout: 10000 // 10s request timeout
                    }
                );
                console.log(`✅ Delivered message from ${sender} to Flask (${response.status})`);
            } catch (error) {
                console.error(`❌ Error contacting Flask backend at ${FLASK_WEBHOOK_URL}:`, error.response?.data || error.message);
            }
        }
    });
}

startHttpServer();
startBot();
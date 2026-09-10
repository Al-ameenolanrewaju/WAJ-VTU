const { default: makeWASocket, DisconnectReason, BufferJSON, initAuthCreds } = require('@whiskeysockets/baileys');
const { createClient } = require('@supabase/supabase-js');
const { Boom } = require('@hapi/boom');
const qrcode = require('qrcode-terminal');
const qrImage = require('qrcode');
const axios = require('axios');
const http = require('http');

const FLASK_WEBHOOK_URL = process.env.FLASK_WEBHOOK_URL || 'http://localhost:5000/whatsapp/webhook';
const BRIDGE_API_TOKEN = process.env.BRIDGE_API_TOKEN || '';
const PORT = Number(process.env.PORT || 3000);
const PAIRING_PHONE_NUMBER = process.env.PAIRING_PHONE_NUMBER || '';

// --- ENVIRONMENT VARIABLE SANITIZATION & VALIDATION ---
let rawSupabaseUrl = (process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL || '').trim().replace(/^["']|["']$/g, '');
const SUPABASE_SERVICE_ROLE_KEY = (process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_KEY || process.env.SUPABASE_ANON_KEY || '').trim().replace(/^["']|["']$/g, '');

// Auto-prepend https:// if protocol was omitted in environment variables
if (rawSupabaseUrl && !rawSupabaseUrl.startsWith('http://') && !rawSupabaseUrl.startsWith('https://')) {
    rawSupabaseUrl = `https://${rawSupabaseUrl}`;
}

const SUPABASE_URL = rawSupabaseUrl;

if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY) {
    console.error('❌ ERROR: Missing Supabase environment variables on Render!');
    console.error(`SUPABASE_URL present: ${Boolean(SUPABASE_URL)}`);
    console.error(`SUPABASE_SERVICE_ROLE_KEY present: ${Boolean(SUPABASE_SERVICE_ROLE_KEY)}`);
    process.exit(1);
}

if (!SUPABASE_URL.startsWith('http://') && !SUPABASE_URL.startsWith('https://')) {
    console.error(`❌ ERROR: SUPABASE_URL must be an HTTPS URL (e.g., https://xyz.supabase.co), but received: "${SUPABASE_URL}"`);
    console.error('👉 Ensure you copy the Project API URL from Supabase Settings > API, NOT the Postgres connection string.');
    process.exit(1);
}

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

let sock;
let whatsappConnected = false;
let latestQrDataUrl = null;

// --- SUPABASE AUTH STATE HANDLER ---
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
    const server = http.createServer((request, response) => {
        const url = new URL(request.url, `http://${request.headers.host || 'localhost'}`);

        // 1. HEALTH CHECK & ROOT ENDPOINTS FOR UPTIMEROBOT (Allows GET and HEAD)
        if ((request.method === 'GET' || request.method === 'HEAD') && (url.pathname === '/health' || url.pathname === '/')) {
            response.writeHead(200, {
                'Content-Type': 'text/plain',
                'Cache-Control': 'no-cache'
            });
            return response.end('OK');
        }

        // 2. PUBLIC QR DISPLAY ENDPOINTS
        if (request.method === 'GET' && (url.pathname === '/qr' || url.pathname === '/qr/image')) {
            if (!latestQrDataUrl) return sendJson(response, 409, { error: 'QR code is not currently available or already connected' });
            if (url.pathname === '/qr/image') {
                const image = Buffer.from(latestQrDataUrl.split(',')[1], 'base64');
                response.writeHead(200, { 'Content-Type': 'image/png', 'Cache-Control': 'no-store' });
                return response.end(image);
            }
            return sendQrPage(response);
        }

        // 3. PROTECTED API ENDPOINTS FOR FLASK BACKEND
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
                console.error('Error sending WhatsApp message:', error.message);
                return sendJson(response, 500, { error: 'Failed to send message' });
            }
        });
    });

    server.listen(PORT, '0.0.0.0', () => {
        console.log(`WhatsApp bridge HTTP server listening on port ${PORT}`);
    });
}

async function startBot() {
    const { state, saveCreds } = await useSupabaseAuthState('main_session');

    sock = makeWASocket({
        auth: state,
        printQRInTerminal: false
    });

    sock.ev.on('creds.update', saveCreds);

    if (PAIRING_PHONE_NUMBER && !sock.authState.creds.registered) {
        setTimeout(async () => {
            try {
                const pairingCode = await sock.requestPairingCode(PAIRING_PHONE_NUMBER.replace(/[^0-9]/g, ''));
                console.log('\n=============================================');
                console.log(`PAIRING CODE: ${pairingCode}`);
                console.log('=============================================\n');
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
                .catch((error) => console.error('Unable to render QR image:', error.message));
        }
        if (connection === 'close') {
            whatsappConnected = false;
            const shouldReconnect = (lastDisconnect?.error instanceof Boom)
                ? lastDisconnect.error.output?.statusCode !== DisconnectReason.loggedOut
                : true;
            if (shouldReconnect) startBot();
        } else if (connection === 'open') {
            whatsappConnected = true;
            latestQrDataUrl = null;
            console.log('✅ WhatsApp Bridge Connected Successfully!');
        }
    });

    sock.ev.on('messages.upsert', async ({ messages }) => {
        const msg = messages[0];
        if (!msg.message || msg.key.fromMe) return;

        const sender = msg.key.remoteJid;
        const text = msg.message.conversation || msg.message.extendedTextMessage?.text || '';

        if (text) {
            try {
                await axios.post(FLASK_WEBHOOK_URL, {
                    from: sender,
                    body: text,
                    fromMe: false
                }, {
                    headers: BRIDGE_API_TOKEN ? { Authorization: `Bearer ${BRIDGE_API_TOKEN}` } : {}
                });
            } catch (error) {
                console.error('Error contacting Flask backend:', error.message);
            }
        }
    });
}

startHttpServer();
startBot();
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const http = require('http');
const path = require('path');

const FLASK_WEBHOOK_URL = process.env.FLASK_WEBHOOK_URL || 'http://localhost:5000/whatsapp/webhook';
const BRIDGE_API_TOKEN = process.env.BRIDGE_API_TOKEN || '';
const PORT = Number(process.env.PORT || 3000);
let sock;
let whatsappConnected = false;

function sendJson(response, statusCode, body) {
    response.writeHead(statusCode, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify(body));
}

function startHttpServer() {
    const server = http.createServer((request, response) => {
        if (request.method !== 'POST' || request.url !== '/api/sendText') {
            return sendJson(response, 404, { error: 'Not found' });
        }
        if (!BRIDGE_API_TOKEN || request.headers.authorization !== `Bearer ${BRIDGE_API_TOKEN}`) {
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
    const { state, saveCreds } = await useMultiFileAuthState(path.join(__dirname, 'auth_info'));
    sock = makeWASocket({
        auth: state,
        printQRInTerminal: false
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        if (qr) {
            qrcode.generate(qr, { small: true });
        }
        if (connection === 'close') {
            whatsappConnected = false;
            const shouldReconnect = (lastDisconnect?.error instanceof Boom)
                ? lastDisconnect.error.output?.statusCode !== DisconnectReason.loggedOut
                : true;
            if (shouldReconnect) startBot();
        } else if (connection === 'open') {
            whatsappConnected = true;
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
                // Forward incoming message to Flask backend
                await axios.post(FLASK_WEBHOOK_URL, {
                    from: sender,
                    body: text,
                    fromMe: false
                }, {
                    headers: { Authorization: `Bearer ${BRIDGE_API_TOKEN}` }
                });
            } catch (error) {
                console.error('Error contacting Flask backend:', error.message);
            }
        }
    });
}

startHttpServer();
startBot();
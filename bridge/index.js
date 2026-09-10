const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const qrcode = require('qrcode-terminal');
const qrImage = require('qrcode');
const axios = require('axios');
const http = require('http');
const path = require('path');

const FLASK_WEBHOOK_URL = process.env.FLASK_WEBHOOK_URL || 'http://localhost:5000/whatsapp/webhook';
const BRIDGE_API_TOKEN = process.env.BRIDGE_API_TOKEN || '';
const PORT = Number(process.env.PORT || 3000);
const PAIRING_PHONE_NUMBER = process.env.PAIRING_PHONE_NUMBER || ''; // e.g., '2348012345678'
let sock;
let whatsappConnected = false;
let latestQrDataUrl = null;
// Quick response for UptimeRobot
app.get('/health', (req, res) => {
  res.status(200).send('OK');
});

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

        // PUBLIC QR DISPLAY ENDPOINTS (Unprotected for easy browser scanning)
        if (request.method === 'GET' && (url.pathname === '/qr' || url.pathname === '/qr/image')) {
            if (!latestQrDataUrl) return sendJson(response, 409, { error: 'QR code is not currently available or already connected' });
            if (url.pathname === '/qr/image') {
                const image = Buffer.from(latestQrDataUrl.split(',')[1], 'base64');
                response.writeHead(200, { 'Content-Type': 'image/png', 'Cache-Control': 'no-store' });
                return response.end(image);
            }
            return sendQrPage(response);
        }

        // PROTECTED API ENDPOINTS FOR FLASK BACKEND
        if (request.method !== 'POST' || request.url !== '/api/sendText') {
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
    const { state, saveCreds } = await useMultiFileAuthState(path.join(__dirname, 'auth_info'));

    sock = makeWASocket({
        auth: state,
        printQRInTerminal: false
    });

    sock.ev.on('creds.update', saveCreds);

    // PAIRING CODE FALLBACK (If phone number environment variable is set)
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
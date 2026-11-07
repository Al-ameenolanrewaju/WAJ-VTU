// --- PWA INSTALL PROMPT ---
let deferredPrompt = null;

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js', { scope: '/' }).catch(() => {
      navigator.serviceWorker.register('/static/service-worker.js').catch(() => {});
    });
  });
}

window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  deferredPrompt = event;

  const installButton = document.querySelector('[data-install-app]');
  if (installButton) {
    installButton.hidden = false;
    installButton.addEventListener('click', async () => {
      if (!deferredPrompt) {
        return;
      }
      deferredPrompt.prompt();
      await deferredPrompt.userChoice;
      deferredPrompt = null;
      installButton.hidden = true;
    }, { once: true });
  }
});

window.addEventListener('appinstalled', () => {
  const installButton = document.querySelector('[data-install-app]');
  if (installButton) {
    installButton.hidden = true;
  }
});


// --- WEB PUSH NOTIFICATIONS ---
function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).split('-').join('+').split('_').join('/');
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

function arrayBufferToBase64(buffer) {
  let binary = '';
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return window.btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
}

async function getOrRegisterServiceWorker() {
  if (!('serviceWorker' in navigator)) return null;
  try {
    let reg = await navigator.serviceWorker.getRegistration('/');
    if (!reg) {
      reg = await navigator.serviceWorker.register('/service-worker.js', { scope: '/' });
    }
    await navigator.serviceWorker.ready;
    return reg;
  } catch (err) {
    try {
      const reg = await navigator.serviceWorker.register('/static/service-worker.js');
      await navigator.serviceWorker.ready;
      return reg;
    } catch (e) {
      return null;
    }
  }
}

async function subscribeUserToPush() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    return false;
  }
  try {
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
      return false;
    }

    const reg = await getOrRegisterServiceWorker();
    if (!reg) return false;

    let sub = await reg.pushManager.getSubscription();

    if (!sub) {
      let vapidPublicKey = '';
      const keyMeta = document.querySelector('meta[name="vapid-public-key"]');
      if (keyMeta && keyMeta.content) {
        vapidPublicKey = keyMeta.content.trim();
      }
      if (!vapidPublicKey) {
        const keyResp = await fetch('/api/push/vapid-public-key');
        const keyData = await keyResp.json();
        vapidPublicKey = keyData.publicKey;
      }

      if (!vapidPublicKey) {
        console.warn('[push] VAPID public key not found');
        return false;
      }

      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
      });
    }

    const rawJson = sub.toJSON ? sub.toJSON() : {};
    const p256dhKey = sub.getKey ? sub.getKey('p256dh') : null;
    const authKey = sub.getKey ? sub.getKey('auth') : null;

    const payload = {
      endpoint: sub.endpoint,
      keys: {
        p256dh: (rawJson.keys && rawJson.keys.p256dh) || (p256dhKey ? arrayBufferToBase64(p256dhKey) : ''),
        auth: (rawJson.keys && rawJson.keys.auth) || (authKey ? arrayBufferToBase64(authKey) : ''),
      }
    };

    const resp = await fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const resJson = await resp.json().catch(() => ({}));
    console.log('[push] Subscription status:', resJson);
    return resp.ok;
  } catch (err) {
    console.warn('[push] Push subscription error:', err);
    return false;
  }
}

async function unsubscribeUserFromPush() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    return false;
  }
  try {
    const reg = await getOrRegisterServiceWorker();
    if (!reg) return false;

    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      const endpoint = sub.endpoint;
      await sub.unsubscribe();
      await fetch('/api/push/unsubscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ endpoint: endpoint }),
      });
    }
    return true;
  } catch (err) {
    console.warn('[push] Unsubscribe error:', err);
    return false;
  }
}

async function isPushSubscribed() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
    return false;
  }
  if (Notification.permission !== 'granted') {
    return false;
  }
  try {
    const reg = await getOrRegisterServiceWorker();
    if (!reg) return false;
    const sub = await reg.pushManager.getSubscription();
    return !!sub;
  } catch (e) {
    return false;
  }
}

// Make functions globally available
window.subscribeUserToPush = subscribeUserToPush;
window.unsubscribeUserFromPush = unsubscribeUserFromPush;
window.isPushSubscribed = isPushSubscribed;

function initPushNotificationPrompt() {
  if (!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)) {
    return;
  }

  if (Notification.permission === 'default' && !sessionStorage.getItem('dismissed_push_prompt')) {
    setTimeout(() => {
      if (document.getElementById('push-notification-banner')) return;

      const banner = document.createElement('div');
      banner.id = 'push-notification-banner';
      banner.style.cssText = 'position:fixed; bottom:24px; left:20px; right:20px; max-width:400px; margin:0 auto; background:#09090b; color:#ffffff; padding:16px 18px; border-radius:12px; border:1px solid #27272a; box-shadow:0 12px 30px rgba(0,0,0,0.4); z-index:99999; display:flex; flex-direction:column; gap:10px; font-family:Inter,sans-serif;';
      banner.innerHTML = `
        <div style="display:flex; align-items:flex-start; gap:12px;">
          <span style="font-size:24px;">🔔</span>
          <div style="flex:1;">
            <div style="font-weight:700; font-size:14px; margin-bottom:2px;">Get Daily Deal & Price Alerts!</div>
            <div style="font-size:12px; color:#a1a1aa; line-height:1.4;">Be the first to know when MTN, Glo, or Airtel drop prices. No spam, ever.</div>
          </div>
        </div>
        <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:4px;">
          <button type="button" id="push-dismiss-btn" style="background:transparent; border:none; color:#a1a1aa; padding:6px 12px; font-size:12px; font-weight:600; cursor:pointer;">Later</button>
          <button type="button" id="push-enable-btn" style="background:#ffc400; color:#09090b; border:none; border-radius:6px; padding:6px 16px; font-size:12px; font-weight:700; cursor:pointer;">Turn On Alerts</button>
        </div>
      `;
      document.body.appendChild(banner);

      const enableBtn = document.getElementById('push-enable-btn');
      if (enableBtn) {
        enableBtn.addEventListener('click', async () => {
          enableBtn.disabled = true;
          enableBtn.textContent = 'Enabling...';
          const success = await subscribeUserToPush();
          if (success) {
            enableBtn.textContent = 'Alerts Active! 🔔';
            setTimeout(() => banner.remove(), 1200);
          } else {
            banner.remove();
          }
        });
      }

      const dismissBtn = document.getElementById('push-dismiss-btn');
      if (dismissBtn) {
        dismissBtn.addEventListener('click', () => {
          sessionStorage.setItem('dismissed_push_prompt', '1');
          banner.remove();
        });
      }
    }, 3000);
  }
}

window.addEventListener('load', () => {
  if ('Notification' in window && Notification.permission === 'granted') {
    // Automatically keep push subscription in sync if user already allowed
    subscribeUserToPush().catch(() => {});
  } else {
    initPushNotificationPrompt();
  }
});

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/static/service-worker.js').catch(() => {
      // Ignore service worker registration failures; the app still works as a normal web app.
    });
  });
}

let deferredPrompt = null;

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
  const base64 = (base64String + padding).replace(/\-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
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

    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();

    if (!sub) {
      const keyMeta = document.querySelector('meta[name="vapid-public-key"]');
      let vapidPublicKey = keyMeta ? keyMeta.content : '';
      if (!vapidPublicKey) {
        const keyResp = await fetch('/api/push/vapid-public-key');
        const keyData = await keyResp.json();
        vapidPublicKey = keyData.publicKey;
      }

      if (!vapidPublicKey) return false;

      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
      });
    }

    await fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub),
    });

    return true;
  } catch (err) {
    console.warn('Push subscription failed:', err);
    return false;
  }
}

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
          banner.remove();
          await subscribeUserToPush();
        });
      }

      const dismissBtn = document.getElementById('push-dismiss-btn');
      if (dismissBtn) {
        dismissBtn.addEventListener('click', () => {
          sessionStorage.setItem('dismissed_push_prompt', '1');
          banner.remove();
        });
      }
    }, 4000);
  }
}

window.addEventListener('load', initPushNotificationPrompt);

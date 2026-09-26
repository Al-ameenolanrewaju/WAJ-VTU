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

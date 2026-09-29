/* Set the style before the first paint: doing it after bootstrap arrives would animate
   every token at once, which reads as a colour flash on launch.

   A separate file rather than an inline <script> so the server's Content-Security-Policy can be
   `script-src 'self'` with no 'unsafe-inline'. A classic script in <head> still runs before the body
   is parsed, so the timing this depends on is unchanged. */
(function () {
  const q = new URLSearchParams(location.search);
  const saved = JSON.parse(localStorage.getItem('ui.prefs') || '{}');
  const root = document.documentElement;
  root.dataset.style = q.get('style') || saved.style || 'claude';
  const mode = q.get('theme') || saved.theme || 'light';
  root.dataset.theme = mode === 'auto'
    ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : mode;
  // The collapsed state is the window's own choice too: applying it after bootstrap arrives
  // would show the sidebar for a frame and then snap it away.
  if (saved.collapsed) root.classList.add('side-collapsed');
  root.classList.add('booting');
})();

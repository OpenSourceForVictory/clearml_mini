// Only trusted user interaction renews a session; API polling never calls renew.
const IDLE = 30 * 60 * 1000;
const ACTIVITY = 'eval-last-activity';
function expiry(token: string): number | null {
  try { const value = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))).exp;
    return typeof value === 'number' ? value * 1000 : null;
  } catch { return null; }
}
export function expireSession(expected: string | null = sessionStorage.getItem('eval-token')) {
  if (!expected || sessionStorage.getItem('eval-token') !== expected) return;
  sessionStorage.removeItem('eval-token');
  window.dispatchEvent(new Event('eval-session-expired'));
}
export function rememberLogin(token: string) {
  sessionStorage.setItem('eval-token', token);
  sessionStorage.setItem(ACTIVITY, String(Date.now()));
  window.dispatchEvent(new Event('eval-session-resumed'));
}
export function clearLogin() {
  sessionStorage.removeItem('eval-token');
  sessionStorage.removeItem(ACTIVITY);
}
export function watchSession() {
  let lastActivity = Number(sessionStorage.getItem(ACTIVITY)) || Date.now();
  let lastAttempt = 0, pending = false, stopped = false;
  function check() {
    const token = sessionStorage.getItem('eval-token');
    if (!token) return;
    const deadline = Math.min(lastActivity + IDLE, expiry(token) ?? Infinity);
    if (Date.now() >= deadline) expireSession(token);
    window.dispatchEvent(new CustomEvent('eval-session-warning', {detail: deadline - Date.now() <= 120000}));
  }
  async function interact(event: Event) {
    if (!event.isTrusted || document.visibilityState !== 'visible') return;
    check();
    const token = sessionStorage.getItem('eval-token');
    if (!token) return;
    lastActivity = Date.now();
    sessionStorage.setItem(ACTIVITY, String(lastActivity));
    // Opaque legacy/test credentials cannot be renewed by this client.
    if (expiry(token) === null || pending || Date.now() - lastAttempt < 60000) return;
    pending = true; lastAttempt = Date.now();
    try {
      const response = await fetch('/api/v1/auth/renew', {method: 'POST', headers: {Authorization: 'Bearer ' + token}, signal: AbortSignal.timeout(10000)});
      if (stopped || sessionStorage.getItem('eval-token') !== token) return;
      if (response.status === 401) { expireSession(token); return; }
      if (!response.ok) return;
      const result = await response.json();
      if (!stopped && sessionStorage.getItem('eval-token') === token && typeof result.access_token === 'string') {
        sessionStorage.setItem('eval-token', result.access_token);
        check();
      }
    } catch { /* Keep drafts and retry on later interaction; expiry still applies. */ }
    finally { pending = false; }
  }
  const events = ['pointerdown', 'keydown', 'wheel', 'touchstart'];
  events.forEach(name => window.addEventListener(name, interact, {capture: true, passive: true}));
  const resume = () => { lastActivity = Number(sessionStorage.getItem(ACTIVITY)) || Date.now(); lastAttempt = 0; check(); };
  window.addEventListener('eval-session-resumed', resume);
  document.addEventListener('visibilitychange', check);
  const timer = window.setInterval(check, 1000);
  check();
  return () => { stopped = true; clearInterval(timer); events.forEach(name => window.removeEventListener(name, interact, true)); window.removeEventListener('eval-session-resumed', resume); document.removeEventListener('visibilitychange', check); };
}

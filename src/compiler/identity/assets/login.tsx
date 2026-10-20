// Compiler-owned local account/password UI. No registration, SMS or external identity flow.
import { createContext, useContext, useEffect, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { currentIdentity, login, logout, IdentityRequestError } from './client';
import type { IdentitySubject } from './client';
const SubjectContext = createContext<IdentitySubject | null>(null);
export function useIdentitySubject(): IdentitySubject | null { return useContext(SubjectContext); }
function failure(error: unknown): string {
  return error instanceof IdentityRequestError && error.status === 401 ? '账号或密码错误'
    : error instanceof IdentityRequestError && error.status === 429 ? '尝试次数过多，请稍后重试'
    : '认证服务暂时不可用，请重试';
}
export function IdentityGate({ children }: { children: ReactNode }) {
  const [subject, setSubject] = useState<IdentitySubject | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let disposed = false;
    currentIdentity().then(session => { if (!disposed) setSubject(session?.subject ?? null); })
      .catch(reason => { if (!disposed) setError(failure(reason)); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy) return;
    setBusy(true); setError('');
    try { const session = await login(username.trim(), password); setPassword(''); setSubject(session.subject); }
    catch (reason) { setError(failure(reason)); }
    finally { setBusy(false); }
  }
  async function signOut() {
    setBusy(true); setError('');
    try { await logout(); setSubject(null); setPassword(''); }
    catch (reason) { setError(failure(reason)); }
    finally { setBusy(false); }
  }
  if (loading) return <p role="status">正在恢复登录状态…</p>;
  if (subject) return <SubjectContext.Provider value={subject}>
    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 16, padding: '8px 24px' }}>
      <span>{subject.display_name || subject.username}</span>
      <button type="button" onClick={signOut} disabled={busy}>退出登录</button>
    </div>
    {error && <p role="alert">{error}</p>}{children}
  </SubjectContext.Provider>;
  return <main style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#f5f7fa' }}>
    <form onSubmit={submit} style={{ display: 'grid', gap: 16, width: 340, maxWidth: '90vw', padding: 32, background: 'white', borderRadius: 8 }}>
      <h1 style={{ margin: 0 }}>账号登录</h1>
      <label htmlFor="arc-login-username">账号</label>
      <input id="arc-login-username" name="username" autoComplete="username" required maxLength={256}
        value={username} onChange={event => setUsername(event.target.value)} disabled={busy} />
      <label htmlFor="arc-login-password">密码</label>
      <input id="arc-login-password" name="password" type="password" autoComplete="current-password" required
        value={password} onChange={event => setPassword(event.target.value)} disabled={busy} />
      {error && <p role="alert">{error}</p>}
      <button type="submit" disabled={busy}>{busy ? '正在登录…' : '登录'}</button>
    </form>
  </main>;
}

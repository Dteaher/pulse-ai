import { useEffect, useRef, useState } from 'react';
import { LockKeyhole, LoaderCircle } from 'lucide-react';
import { api, hasAccessToken, setAccessToken } from '../services/api';

export default function AccessGate({ required }: { required: boolean }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (required && !hasAccessToken()) setOpen(true);
    const reopen = () => setOpen(true);
    window.addEventListener('pulse:access-required', reopen);
    return () => window.removeEventListener('pulse:access-required', reopen);
  }, [required]);
  useEffect(() => {
    if (open && !dialog.current?.open) {
      dialog.current?.showModal();
      input.current?.focus();
    }
    if (!open) dialog.current?.close();
  }, [open]);
  return (
    <dialog
      ref={dialog}
      className="access-dialog"
      aria-labelledby="access-title"
      onCancel={(e) => e.preventDefault()}
    >
      <div className="access-icon">
        <LockKeyhole size={22} />
      </div>
      <h2 id="access-title">Вход в рабочую область</h2>
      <p>Введите код, который предоставил владелец демо. Код действует до закрытия этой вкладки.</p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy) return;
          setBusy(true);
          setError('');
          setAccessToken(value.trim());
          try {
            await api('access/check');
            setValue('');
            setOpen(false);
          } catch {
            setAccessToken('');
            setError('Не удалось войти. Проверьте код и доступность сервера.');
          } finally {
            setBusy(false);
          }
        }}
      >
        <label htmlFor="access-code">Код доступа</label>
        <input
          ref={input}
          id="access-code"
          type="password"
          autoComplete="off"
          required
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-describedby={error ? 'access-error' : undefined}
        />
        {error && (
          <p id="access-error" className="access-error" role="alert">
            {error}
          </p>
        )}
        <button className="primary" type="submit" disabled={busy || !value.trim()}>
          {busy ? (
            <>
              <LoaderCircle className="spin" size={16} /> Проверяем…
            </>
          ) : (
            'Открыть PULSE'
          )}
        </button>
      </form>
      <small>Это код рабочей области, а не API-ключ модели.</small>
    </dialog>
  );
}

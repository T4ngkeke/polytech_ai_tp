/**
 * PasswordInput.jsx — a password field with an eye button that shows the
 * typed text, so the user can check it. Takes the props of an <input>;
 * `wrapperClassName` goes on the box around it (margins).
 */
import { useState } from 'react';
import Icon from './Icon';
import { useT } from '../lib/i18n';

export default function PasswordInput({ className = '', wrapperClassName = '', ...props }) {
  const t = useT();
  const [shown, setShown] = useState(false);
  return (
    <div className={`relative ${wrapperClassName}`}>
      <input {...props} type={shown ? 'text' : 'password'} className={`${className} pr-10`}
        autoCapitalize="off" autoCorrect="off" spellCheck={false} />
      <button type="button" onClick={() => setShown(!shown)} aria-pressed={shown}
        aria-label={t(shown ? 'password.hide' : 'password.show')} title={t(shown ? 'password.hide' : 'password.show')}
        className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-xl text-cream-muted hover:text-cream">
        <Icon name={shown ? 'eyeOff' : 'eye'} className="h-4 w-4" />
      </button>
    </div>
  );
}

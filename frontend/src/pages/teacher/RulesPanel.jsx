/**
 * teacher/RulesPanel.jsx — "Rules" tab of a class or lab, with the rules inherited from above.
 */
import { useState, useEffect } from 'react';
import api from '../../lib/api';
import RuleList from '../../components/RuleList';
import { useT } from '../../lib/i18n';

export default function RulesPanel({ level, targetId, classId, context }) {
  const t = useT();
  // A lab shows the class rules it inherits above its own.
  const [inherited, setInherited] = useState([]);
  useEffect(() => {
    if (level !== 'lab') { setInherited([]); return; }
    api.get(`/api/teacher/rule-items?level=class&target_id=${classId}`)
      .then((items) => setInherited(items.filter((r) => r.is_active)))
      .catch(() => setInherited([]));
  }, [level, classId]);
  return (
    <div className="max-w-2xl space-y-4">
      <div>
        <h3 className="mb-1 text-[15px] font-semibold text-cream">{t('t.rules.title')}</h3>
        <p className="text-[13px] text-cream-muted">
          {t(level === 'class' ? 't.rules.forClass' : 't.rules.forLab', { name: context })}
        </p>
      </div>
      <RuleList key={`${level}-${targetId}`} level={level} targetId={targetId} classId={classId}
        inherited={inherited} inheritedLabel={t('t.rules.inherited')} />
    </div>
  );
}

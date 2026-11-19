/**
 * teacher/RulesPanel.jsx — "Rules" tab of a class or lab, with the rules inherited from above.
 */
import { useState, useEffect } from 'react';
import api from '../../lib/api';
import RuleList from '../../components/RuleList';

export default function RulesPanel({ level, targetId, classId, context }) {
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
        <h3 className="text-sm font-semibold text-cream mb-1">Rules for the tutor</h3>
        <p className="text-xs text-cream-muted">
          {level === 'class' ? 'For every TP of ' : 'For this TP only: '}
          <span className="text-cyan font-mono">{context}</span>. The tutor follows the rules that are on.
        </p>
      </div>
      <RuleList key={`${level}-${targetId}`} level={level} targetId={targetId} classId={classId}
        inherited={inherited} inheritedLabel="From the class (change them in the class Rules tab)" />
    </div>
  );
}

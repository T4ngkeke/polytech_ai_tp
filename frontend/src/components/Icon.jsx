/**
 * Line icons (lucide-react) by a short name, so a name can live in data such
 * as the admin model map. Icons use currentColor and follow the theme.
 */
import {
  Archive, ArrowUpDown, Bot, Check, Compass, Copy, Download, Eye, FileText, KeyRound, Layers,
  Lock, LockOpen, MessageSquare, Network, Pencil, Repeat, ThumbsDown, ThumbsUp, Trash2, TriangleAlert,
} from 'lucide-react';

const ICONS = {
  trash: Trash2, file: FileText, layers: Layers, warning: TriangleAlert, lock: Lock, unlock: LockOpen,
  pencil: Pencil, key: KeyRound, transfer: Repeat, archive: Archive, thumbsUp: ThumbsUp,
  thumbsDown: ThumbsDown, bot: Bot, eye: Eye, message: MessageSquare, network: Network,
  sort: ArrowUpDown, compass: Compass, download: Download, copy: Copy, check: Check,
};

export default function Icon({ name, className = 'w-4 h-4', title }) {
  const Shape = ICONS[name];
  if (!Shape) return null;
  return <Shape className={className} aria-hidden={title ? undefined : 'true'} aria-label={title} />;
}

/**
 * Line icons (lucide-react) by a short name, so a name can live in data such
 * as the admin model map. Icons use currentColor and follow the theme.
 */
import {
  Activity, Archive, ArrowRightLeft, ArrowUpDown, Bot, Check, ChevronRight, Compass, Copy, Cpu, Database,
  Download, Ellipsis, Eye, EyeOff, FileText, KeyRound, Layers, LayoutGrid, Lock, LockOpen, LogOut, Menu,
  MessageSquare, Network, Pencil, Plus, Repeat, RotateCcw, Search, Send, Settings, Square, ThumbsDown,
  ThumbsUp, Trash2, TriangleAlert, Upload, Users, X,
} from 'lucide-react';

const ICONS = {
  trash: Trash2, file: FileText, layers: Layers, warning: TriangleAlert, lock: Lock, unlock: LockOpen,
  pencil: Pencil, key: KeyRound, transfer: Repeat, archive: Archive, thumbsUp: ThumbsUp,
  thumbsDown: ThumbsDown, bot: Bot, eye: Eye, message: MessageSquare, network: Network,
  sort: ArrowUpDown, compass: Compass, download: Download, copy: Copy, check: Check, close: X, reset: RotateCcw,
  menu: Menu, send: Send, stop: Square, plus: Plus, chevronRight: ChevronRight, ellipsis: Ellipsis,
  logout: LogOut, settings: Settings, users: Users, grid: LayoutGrid, pulse: Activity, chip: Cpu,
  database: Database, upload: Upload, search: Search, eyeOff: EyeOff, swap: ArrowRightLeft,
};

export default function Icon({ name, className = 'w-4 h-4', title }) {
  const Shape = ICONS[name];
  if (!Shape) return null;
  return <Shape className={className} aria-hidden={title ? undefined : 'true'} aria-label={title} />;
}

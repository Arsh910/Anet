import React from 'react'
import {
  Network, Code2, Search, Folder, Monitor, ShieldCheck, Sparkles, Bot,
} from 'lucide-react'

const MAP = {
  'org-chart': Network,
  'code-brackets': Code2,
  'megaphone': Search,
  'folder': Folder,
  'monitor': Monitor,
  'shield-check': ShieldCheck,
  'sparkles': Sparkles,
}

export default function AgentIcon({ name, size = 16 }) {
  const Cmp = MAP[name] || Bot
  return <Cmp size={size} strokeWidth={1.75} />
}

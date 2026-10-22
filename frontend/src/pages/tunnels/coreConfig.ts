// ─── Tunnels Core Configurations & Design Metadata ────────────────────────

export interface CoreOption {
  value: string
  label: string
  description?: string
}

export interface CoreThemeConfig {
  coreId: 'gost' | 'rathole' | 'backhaul' | 'chisel' | 'frp'
  title: string
  subtitle: string
  badgeText: string
  iconName: 'Zap' | 'Rocket' | 'RadioTower' | 'Terminal' | 'Layers'
  colorScheme: 'indigo' | 'amber' | 'emerald' | 'cyan' | 'purple'
  bgGradientClass: string
  borderClass: string
  headerBadgeClass: string
  iconBoxClass: string
  activeRingClass: string
  typeOptions: CoreOption[]
  transportOptions: CoreOption[]
}

export const CORE_CONFIGS: Record<string, CoreThemeConfig> = {
  gost: {
    coreId: 'gost',
    title: 'GOST v3.0 Core Settings',
    subtitle: 'Next-Gen Anti-DPI & High-Performance Routing Core',
    badgeText: 'GOST v3.0 Core',
    iconName: 'Zap',
    colorScheme: 'indigo',
    bgGradientClass:
      'bg-gradient-to-br from-indigo-50/60 via-gray-50 to-purple-50/40 dark:from-indigo-950/20 dark:via-gray-800/60 dark:to-purple-950/20',
    borderClass: 'border-indigo-200/70 dark:border-indigo-900/40',
    headerBadgeClass:
      'bg-indigo-100/80 text-indigo-700 dark:bg-indigo-900/60 dark:text-indigo-300 border-indigo-200 dark:border-indigo-800',
    iconBoxClass:
      'bg-indigo-100 dark:bg-indigo-900/60 text-indigo-600 dark:text-indigo-300',
    activeRingClass:
      'border-indigo-400 dark:border-indigo-500 ring-2 ring-indigo-400/20',
    typeOptions: [
      { value: 'tcp', label: 'TCP' },
      { value: 'udp', label: 'UDP' },
      { value: 'grpc', label: 'gRPC' },
      { value: 'tcpmux', label: 'TCP Mux' },
    ],
    transportOptions: [
      { value: 'tcp', label: 'TCP (Standard)' },
      { value: 'ws', label: 'WebSocket (WS)' },
      { value: 'wss', label: 'WebSocket Secure (WSS)' },
      { value: 'mws', label: 'Multiplex WS (MWS)' },
      { value: 'mwss', label: 'Multiplex WSS' },
      { value: 'quic', label: 'QUIC (HTTP/3 UDP, 0-RTT)' },
      { value: 'grpc', label: 'gRPC (Multiplexed Stealth)' },
      { value: 'kcp', label: 'KCP (Anti-Packet-Loss ARQ)' },
      { value: 'ssh', label: 'SSH (Encrypted Subsystem)' },
    ],
  },
  rathole: {
    coreId: 'rathole',
    title: 'Rathole Core Settings',
    subtitle: 'Ultra-Lightweight, Secure NAT-Traversal Core in Rust',
    badgeText: 'Rathole Core',
    iconName: 'Rocket',
    colorScheme: 'amber',
    bgGradientClass:
      'bg-gradient-to-br from-amber-50/60 via-gray-50 to-orange-50/40 dark:from-amber-950/20 dark:via-gray-800/60 dark:to-orange-950/20',
    borderClass: 'border-amber-200/70 dark:border-amber-900/40',
    headerBadgeClass:
      'bg-amber-100/80 text-amber-700 dark:bg-amber-900/60 dark:text-amber-300 border-amber-200 dark:border-amber-800',
    iconBoxClass:
      'bg-amber-100 dark:bg-amber-900/60 text-amber-600 dark:text-amber-300',
    activeRingClass:
      'border-amber-400 dark:border-amber-500 ring-2 ring-amber-400/20',
    typeOptions: [
      { value: 'tcp', label: 'TCP' },
      { value: 'udp', label: 'UDP (Gaming)' },
      { value: 'tcp+udp', label: 'TCP + UDP' },
    ],
    transportOptions: [
      { value: 'tcp', label: 'TCP (Standard Raw)' },
      { value: 'noise', label: 'Noise Protocol (Encrypted / Gaming / Anti-DPI)' },
      { value: 'ws', label: 'WebSocket (WS)' },
      { value: 'wss', label: 'WebSocket + TLS (WSS)' },
    ],
  },
  backhaul: {
    coreId: 'backhaul',
    title: 'Backhaul Multiplexing Settings',
    subtitle: 'High-Performance Reverse Tunnel with Yamux & WebSockets',
    badgeText: 'Backhaul v0.6+ Core',
    iconName: 'RadioTower',
    colorScheme: 'emerald',
    bgGradientClass:
      'bg-gradient-to-br from-emerald-50/60 via-gray-50 to-teal-50/40 dark:from-emerald-950/20 dark:via-gray-800/60 dark:to-teal-950/20',
    borderClass: 'border-emerald-200/70 dark:border-emerald-900/40',
    headerBadgeClass:
      'bg-emerald-100/80 text-emerald-700 dark:bg-emerald-900/60 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800',
    iconBoxClass:
      'bg-emerald-100 dark:bg-emerald-900/60 text-emerald-600 dark:text-emerald-300',
    activeRingClass:
      'border-emerald-400 dark:border-emerald-500 ring-2 ring-emerald-400/20',
    typeOptions: [
      { value: 'tcp', label: 'TCP (Standard)' },
      { value: 'udp', label: 'UDP (Gaming / Anti-Lag)' },
      { value: 'tcp+udp', label: 'TCP + UDP (Dual Forward)' },
    ],
    transportOptions: [
      { value: 'tcp', label: 'TCP' },
      { value: 'tcpmux', label: 'TCP Mux (Yamux)' },
      { value: 'ws', label: 'WebSocket (WS)' },
      { value: 'wsmux', label: 'WebSocket Mux' },
      { value: 'wss', label: 'WSS (Secure WebSocket)' },
      { value: 'wssmux', label: 'WSS Mux' },
    ],
  },
  chisel: {
    coreId: 'chisel',
    title: 'Chisel Advanced Settings',
    subtitle: 'High-Speed WebSocket Tunnel with SSH Multiplexing & Decoy Camouflage',
    badgeText: 'Chisel Core',
    iconName: 'Terminal',
    colorScheme: 'cyan',
    bgGradientClass:
      'bg-gradient-to-br from-cyan-50/60 via-gray-50 to-teal-50/40 dark:from-cyan-950/20 dark:via-gray-800/60 dark:to-teal-950/20',
    borderClass: 'border-cyan-200/70 dark:border-cyan-900/40',
    headerBadgeClass:
      'bg-cyan-100/80 text-cyan-700 dark:bg-cyan-900/60 dark:text-cyan-300 border-cyan-200 dark:border-cyan-800',
    iconBoxClass:
      'bg-cyan-100 dark:bg-cyan-900/60 text-cyan-600 dark:text-cyan-300',
    activeRingClass:
      'border-cyan-400 dark:border-cyan-500 ring-2 ring-cyan-400/20',
    typeOptions: [
      { value: 'tcp', label: 'TCP (Standard)' },
      { value: 'udp', label: 'UDP (Gaming / Anti-Lag)' },
      { value: 'tcp+udp', label: 'TCP + UDP (Dual Forward)' },
      { value: 'socks5', label: 'SOCKS5 (Dynamic Proxy)' },
    ],
    transportOptions: [
      { value: 'ws', label: 'WS - Plain WebSocket (HTTP, Low Overhead)' },
      { value: 'wss', label: 'WSS - Encrypted WebSocket over TLS (HTTPS)' },
    ],
  },
  frp: {
    coreId: 'frp',
    title: 'FRP Advanced Settings',
    subtitle: 'High-Performance Reverse Proxy with Multiplexing & Encryption',
    badgeText: 'FRP v0.50+ Core',
    iconName: 'Layers',
    colorScheme: 'purple',
    bgGradientClass:
      'bg-gradient-to-br from-purple-50/60 via-gray-50 to-indigo-50/40 dark:from-purple-950/20 dark:via-gray-800/60 dark:to-indigo-950/20',
    borderClass: 'border-purple-200/70 dark:border-purple-900/40',
    headerBadgeClass:
      'bg-purple-100/80 text-purple-700 dark:bg-purple-900/60 dark:text-purple-300 border-purple-200 dark:border-purple-800',
    iconBoxClass:
      'bg-purple-100 dark:bg-purple-900/60 text-purple-600 dark:text-purple-300',
    activeRingClass:
      'border-purple-400 dark:border-purple-500 ring-2 ring-purple-400/20',
    typeOptions: [
      { value: 'tcp', label: 'TCP' },
      { value: 'udp', label: 'UDP' },
      { value: 'tcp+udp', label: 'TCP + UDP' },
    ],
    transportOptions: [
      { value: 'tcp', label: 'TCP (with TLS & Zero-Byte Signature)' },
      { value: 'kcp', label: 'KCP (Fast UDP - Resilient to Packet Loss)' },
      { value: 'quic', label: 'QUIC (HTTP/3 UDP + TLS 1.3 Multiplex)' },
      { value: 'websocket', label: 'WebSocket (Plain WS)' },
      { value: 'wss', label: 'WSS (Secure WebSocket - CDN Capable)' },
    ],
  },
}

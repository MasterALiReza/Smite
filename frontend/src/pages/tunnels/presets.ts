// ─── Tunnel Presets Data-Driven Engine ──────────────────────────────────────

export interface TunnelPreset {
  id: string
  name: string
  icon: 'shield' | 'zap' | 'rocket' | 'globe' | 'gamepad' | 'layers'
  colorScheme: 'purple' | 'amber' | 'emerald' | 'blue' | 'cyan' | 'indigo' | 'rose'
  badge?: string
  description: string
  appliedValues: Record<string, any>
  isActive: (currentData: Record<string, any>) => boolean
  toastTitle?: string
  toastMessage: string
}

export const GOST_PRESETS: TunnelPreset[] = [
  {
    id: 'gost-stealth',
    name: 'Stealth Anti-DPI',
    icon: 'shield',
    colorScheme: 'purple',
    badge: 'Recommended',
    description: 'gRPC + uTLS Chrome. Bypasses deep packet inspection (Recommended for Iran).',
    appliedValues: {
      transport_type: 'grpc',
      security_type: 'utls',
      utls_fingerprint: 'chrome',
      stealth_domain: 'www.google.com',
      keepalive_interval: 15,
      gaming_mode: false,
    },
    isActive: (formData) =>
      formData.transport_type === 'grpc' &&
      formData.security_type === 'utls' &&
      !formData.gaming_mode,
    toastTitle: 'Preset Applied',
    toastMessage: 'Stealth Anti-DPI (gRPC + uTLS Chrome) applied',
  },
  {
    id: 'gost-gaming',
    name: 'Ultra-Low Ping',
    icon: 'zap',
    colorScheme: 'amber',
    badge: 'Gaming / UDP',
    description: 'gRPC + uTLS Chrome. 0-RTT Multiplexing & Lowest Jitter (Best for Gaming / UDP).',
    appliedValues: {
      transport_type: 'grpc',
      security_type: 'utls',
      utls_fingerprint: 'chrome',
      stealth_domain: 'www.google.com',
      gaming_mode: true,
      keepalive_interval: 15,
    },
    isActive: (formData) =>
      formData.transport_type === 'grpc' && Boolean(formData.gaming_mode),
    toastTitle: 'Preset Applied',
    toastMessage: 'Ultra-Low Ping (gRPC + uTLS Chrome Gaming) applied',
  },
  {
    id: 'gost-packetloss',
    name: 'Anti-Packet-Loss',
    icon: 'rocket',
    colorScheme: 'emerald',
    badge: 'High Stability',
    description: 'WSS + TLS. Rock-solid TCP/WebSocket framing immune to UDP loss & DPI blocking.',
    appliedValues: {
      transport_type: 'wss',
      security_type: 'tls',
      stealth_domain: 'dl.google.com',
      gaming_mode: false,
      keepalive_interval: 15,
    },
    isActive: (formData) =>
      formData.transport_type === 'wss' ||
      (formData.transport_type === 'ws' && formData.security_type === 'tls'),
    toastTitle: 'Preset Applied',
    toastMessage: 'Anti-Packet-Loss (WSS + TLS) applied',
  },
]

export const RATHOLE_PRESETS: TunnelPreset[] = [
  {
    id: 'rathole-noise',
    name: 'Noise Protocol',
    icon: 'shield',
    colorScheme: 'amber',
    badge: 'Gaming & Anti-DPI',
    description: 'WireGuard-grade encrypted tunnel. Zero handshake lag & optimal UDP forwarding.',
    appliedValues: {
      rathole_transport: 'noise',
      custom_sni: '',
    },
    isActive: (formData) => formData.rathole_transport === 'noise',
    toastTitle: 'Preset Applied',
    toastMessage: 'Noise Protocol (Gaming & Anti-DPI) applied',
  },
  {
    id: 'rathole-wss',
    name: 'WSS Stealth',
    icon: 'globe',
    colorScheme: 'blue',
    badge: 'CDN Ready',
    description: 'HTTPS WebSocket + TLS SNI spoofing for robust anti-DPI firewall evasion.',
    appliedValues: {
      rathole_transport: 'wss',
      custom_sni: 'dl.google.com',
    },
    isActive: (formData) => formData.rathole_transport === 'wss',
    toastTitle: 'Preset Applied',
    toastMessage: 'WSS Camouflage (TLS + CDN Capable) applied',
  },
  {
    id: 'rathole-tcp',
    name: 'TCP Standard',
    icon: 'zap',
    colorScheme: 'purple',
    badge: 'Lowest CPU',
    description: 'Raw direct TCP stream. Lowest possible CPU and memory overhead.',
    appliedValues: {
      rathole_transport: 'tcp',
      custom_sni: '',
    },
    isActive: (formData) =>
      !formData.rathole_transport || formData.rathole_transport === 'tcp',
    toastTitle: 'Preset Applied',
    toastMessage: 'Standard TCP applied',
  },
]

export const CHISEL_PRESETS: TunnelPreset[] = [
  {
    id: 'chisel-stealth',
    name: 'Anti-DPI Stealth',
    icon: 'shield',
    colorScheme: 'cyan',
    badge: 'Active Probe Defense',
    description: 'WSS TLS + speedtest.net decoy camouflage against active probers.',
    appliedValues: {
      chisel_transport: 'wss',
      chisel_backend_url: 'https://speedtest.net',
      chisel_custom_sni: 'speedtest.net',
      chisel_keepalive: '10s',
    },
    isActive: (formData) =>
      formData.chisel_transport === 'wss' && Boolean(formData.chisel_backend_url),
    toastTitle: 'Preset Applied',
    toastMessage: 'Anti-DPI Stealth Preset (WSS + Camouflage + SNI) configured',
  },
  {
    id: 'chisel-gaming',
    name: 'Ultra-Low Ping',
    icon: 'zap',
    colorScheme: 'amber',
    badge: 'Gaming / UDP',
    description: 'UDP Gaming mode + aggressive 5s Keepalive against firewall dropouts.',
    appliedValues: {
      type: 'udp',
      chisel_keepalive: '5s',
    },
    isActive: (formData) =>
      formData.type === 'udp' && formData.chisel_keepalive === '5s',
    toastTitle: 'Preset Applied',
    toastMessage: 'Ultra-Low Ping Gaming (UDP + 5s Keepalive) configured',
  },
  {
    id: 'chisel-cdn',
    name: 'CDN Friendly',
    icon: 'globe',
    colorScheme: 'blue',
    badge: 'Reverse Proxy',
    description: 'HTTP/WS with Host spoofing compatible with reverse proxies & CDNs.',
    appliedValues: {
      chisel_transport: 'ws',
      chisel_custom_host: 'cdn.cloudflare.com',
      chisel_keepalive: '15s',
    },
    isActive: (formData) =>
      formData.chisel_transport === 'ws' &&
      formData.chisel_custom_host === 'cdn.cloudflare.com',
    toastTitle: 'Preset Applied',
    toastMessage: 'CDN-Friendly WebSocket Preset configured',
  },
]

export const FRP_PRESETS: TunnelPreset[] = [
  {
    id: 'frp-gaming',
    name: 'Fast TCP + TLS',
    icon: 'zap',
    colorScheme: 'cyan',
    badge: 'Gaming / Low Jitter',
    description: 'FRP Native TLS over TCP. Fast, low jitter, bypasses DPI (Best for Gaming/UDP).',
    appliedValues: {
      frp_transport: 'tcp',
      frp_encryption: true,
      frp_compression: false,
    },
    isActive: (formData) =>
      (formData.frp_transport === 'tcp' || !formData.frp_transport) &&
      formData.frp_encryption &&
      !formData.frp_compression,
    toastTitle: 'Preset Applied',
    toastMessage: 'TCP + Native TLS Fast Gaming applied',
  },
  {
    id: 'frp-stealth',
    name: 'WSS Stealth',
    icon: 'globe',
    colorScheme: 'indigo',
    badge: 'CDN Friendly',
    description: 'Secure WebSocket + SNI camouflage. CDN friendly & zero signature.',
    appliedValues: {
      frp_transport: 'wss',
      frp_sni: 'dl.google.com',
      frp_encryption: true,
      frp_compression: true,
    },
    isActive: (formData) => formData.frp_transport === 'wss',
    toastTitle: 'Preset Applied',
    toastMessage: 'WSS Stealth / Anti-DPI applied',
  },
  {
    id: 'frp-kcp',
    name: 'KCP Anti-Loss',
    icon: 'rocket',
    colorScheme: 'emerald',
    badge: 'Loss Resistance',
    description: 'Aggressive ARQ retransmission protocol for unstable / high-loss routes.',
    appliedValues: {
      frp_transport: 'kcp',
      frp_encryption: true,
      frp_compression: false,
    },
    isActive: (formData) => formData.frp_transport === 'kcp',
    toastTitle: 'Preset Applied',
    toastMessage: 'KCP Anti-Packet-Loss applied',
  },
]

export const BACKHAUL_PRESETS: TunnelPreset[] = [
  {
    id: 'backhaul-mux',
    name: 'Fast Multiplex',
    icon: 'layers',
    colorScheme: 'emerald',
    badge: 'High Throughput',
    description: 'Fast multiplexed TCP tunnel bundling multiple connection streams smoothly.',
    appliedValues: {
      transport: 'tcpmux',
      accept_udp: false,
      gaming_mode: false,
    },
    isActive: (backhaulState) =>
      backhaulState.transport === 'tcpmux' && !backhaulState.gaming_mode,
    toastTitle: 'Preset Applied',
    toastMessage: 'Fast Multiplex (tcpmux) Preset applied',
  },
  {
    id: 'backhaul-gaming',
    name: 'Ultra-Low Ping Gaming',
    icon: 'zap',
    colorScheme: 'amber',
    badge: 'Gaming / UDP Anti-Lag',
    description: 'Yamux nodelay + 8K channel size + aggressive pool for instant UDP responses.',
    appliedValues: {
      // Upstream Backhaul implements accept_udp only for transport=tcp
      transport: 'tcp',
      accept_udp: true,
      gaming_mode: true,
    },
    isActive: (backhaulState) => Boolean(backhaulState.gaming_mode),
    toastTitle: 'Preset Applied',
    toastMessage: 'Ultra-Low Ping Gaming (Yamux/Nodelay/Aggressive Pool) applied',
  },
  {
    id: 'backhaul-wss',
    name: 'WSS Camouflage',
    icon: 'globe',
    colorScheme: 'blue',
    badge: 'CDN & Anti-DPI',
    description: 'Secure TLS 1.3 WebSocket encapsulation capable of routing via CDN proxies.',
    appliedValues: {
      transport: 'wssmux',
      accept_udp: false,
      gaming_mode: false,
    },
    isActive: (backhaulState) =>
      backhaulState.transport === 'wssmux' || backhaulState.transport === 'wss',
    toastTitle: 'Preset Applied',
    toastMessage: 'WSS CDN-Friendly Camouflage applied',
  },
]

export const CORE_PRESETS: Record<string, TunnelPreset[]> = {
  gost: GOST_PRESETS,
  rathole: RATHOLE_PRESETS,
  chisel: CHISEL_PRESETS,
  frp: FRP_PRESETS,
  backhaul: BACKHAUL_PRESETS,
}

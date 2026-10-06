// ─── Tunnels Domain Types & Shared Helpers ─────────────────────────────────

// ─── Reapply Progress Types ────────────────────────────────────────────────
export type ReapplyStatus = 'pending' | 'running' | 'success' | 'error'

export interface TunnelReapplyState {
  id: string
  name: string
  status: ReapplyStatus
  error?: string
}

export interface TunnelCategory {
  id: string
  name: string
  color: string
  description?: string
  tunnel_count: number
  created_at?: string
}

export interface Tunnel {
  id: string
  name: string
  core: string
  type: string
  node_id: string
  iran_node_id?: string
  foreign_node_id?: string
  spec: Record<string, any>
  status: string
  error_message?: string | null
  revision: number
  category?: string | null
  created_at: string
  updated_at: string
  [key: string]: any
}

export type BackhaulTransport = 'tcp' | 'udp' | 'ws' | 'wsmux' | 'tcpmux' | 'wss' | 'wssmux'

export interface BackhaulFormState {
  transport: BackhaulTransport
  control_port: string
  public_port: string
  listen_ip: string
  public_host: string
  remote_addr: string
  target_host: string
  target_port: string
  token: string
  accept_udp: boolean
  gaming_mode?: boolean
}

export interface BackhaulAdvancedServerState {
  keepalive_period: string
  heartbeat: string
  channel_size: string
  mux_con: string
  log_level: string
  nodelay: boolean
  skip_optz: boolean
  tls_cert: string
  tls_key: string
  sniffer: boolean
  web_port: string
  proxy_protocol: boolean
  mss: string
  so_rcvbuf: string
  so_sndbuf: string
  mux_version: string
  mux_framesize: string
  mux_recievebuffer: string
  mux_streambuffer: string
}

export interface BackhaulAdvancedClientState {
  connection_pool: string
  retry_interval: string
  dial_timeout: string
  keepalive_period: string
  heartbeat: string
  channel_size: string
  log_level: string
  nodelay: boolean
  aggressive_pool: boolean
  edge_ip: string
  skip_optz: boolean
  mss: string
  so_rcvbuf: string
  so_sndbuf: string
  mux_version: string
  mux_framesize: string
  mux_recievebuffer: string
  mux_streambuffer: string
}

export interface BackhaulAdvancedState {
  server: BackhaulAdvancedServerState
  client: BackhaulAdvancedClientState
  customPorts: string
}

export const generateRandomControlPort = (): string => {
  return String(Math.floor(20000 + Math.random() * 25000))
}

export const createDefaultBackhaulState = (): BackhaulFormState => ({
  transport: 'tcp',
  control_port: generateRandomControlPort(),
  public_port: '443',
  listen_ip: '0.0.0.0',
  public_host: '',
  remote_addr: '',
  target_host: '127.0.0.1',
  target_port: '8080',
  token: '',
  accept_udp: false,
  gaming_mode: false,
})

export const createDefaultBackhaulAdvancedState = (): BackhaulAdvancedState => ({
  server: {
    keepalive_period: '20',
    heartbeat: '20',
    channel_size: '2048',
    mux_con: '8',
    log_level: 'info',
    nodelay: true,
    skip_optz: false,
    tls_cert: '',
    tls_key: '',
    sniffer: false,
    web_port: '',
    proxy_protocol: false,
    mss: '',
    so_rcvbuf: '',
    so_sndbuf: '',
    mux_version: '1',
    mux_framesize: '32768',
    mux_recievebuffer: '',
    mux_streambuffer: '',
  },
  client: {
    connection_pool: '8',
    retry_interval: '3',
    dial_timeout: '10',
    keepalive_period: '20',
    heartbeat: '20',
    channel_size: '2048',
    log_level: 'info',
    nodelay: true,
    aggressive_pool: true,
    edge_ip: '',
    skip_optz: false,
    mss: '',
    so_rcvbuf: '',
    so_sndbuf: '',
    mux_version: '1',
    mux_framesize: '32768',
    mux_recievebuffer: '',
    mux_streambuffer: '',
  },
  customPorts: '',
})

export const numericServerKeys = new Set([
  'keepalive_period',
  'heartbeat',
  'channel_size',
  'mux_con',
  'web_port',
  'mss',
  'so_rcvbuf',
  'so_sndbuf',
  'mux_version',
  'mux_framesize',
  'mux_recievebuffer',
  'mux_streambuffer',
])
export const booleanServerKeys = new Set(['nodelay', 'skip_optz', 'sniffer', 'proxy_protocol'])
export const stringServerKeys = new Set(['log_level', 'tls_cert', 'tls_key', 'sniffer_log'])

export const numericClientKeys = new Set([
  'connection_pool',
  'retry_interval',
  'dial_timeout',
  'keepalive_period',
  'heartbeat',
  'channel_size',
  'mss',
  'so_rcvbuf',
  'so_sndbuf',
  'mux_version',
  'mux_framesize',
  'mux_recievebuffer',
  'mux_streambuffer',
])
export const booleanClientKeys = new Set(['nodelay', 'aggressive_pool', 'skip_optz'])
export const stringClientKeys = new Set(['log_level', 'edge_ip'])

export interface BackhaulDisplayInfo {
  controlPort: string
  publicPort: string
  target: string
}

export const getBackhaulDisplayInfo = (spec: Record<string, any> | undefined): BackhaulDisplayInfo => {
  if (!spec) {
    return { controlPort: 'N/A', publicPort: 'N/A', target: 'N/A' }
  }

  const controlPort =
    spec.control_port ||
    (typeof spec.bind_addr === 'string' && spec.bind_addr.includes(':') ? spec.bind_addr.split(':').pop() : undefined) ||
    (typeof spec.remote_addr === 'string' && spec.remote_addr.includes(':') ? spec.remote_addr.split(':').pop() : undefined) ||
    'N/A'

  const publicPort =
    spec.public_port ||
    spec.listen_port ||
    (Array.isArray(spec.ports) && spec.ports.length > 0
      ? (() => {
          const [first] = spec.ports
          if (typeof first !== 'string') return undefined
          const [left] = first.split('=')
          const parts = left.split(':')
          return parts.pop()
        })()
      : undefined) ||
    'N/A'

  const target =
    spec.target_addr ||
    (Array.isArray(spec.ports) && spec.ports.length > 0
      ? (() => {
          const [first] = spec.ports
          if (typeof first !== 'string') return undefined
          const segments = first.split('=')
          return segments.length > 1 ? segments[1] : undefined
        })()
      : undefined) ||
    'N/A'

  return {
    controlPort: controlPort?.toString() || 'N/A',
    publicPort: publicPort?.toString() || 'N/A',
    target: target?.toString() || 'N/A',
  }
}

export const getCategoryColorClasses = (color: string = 'blue') => {
  const map: Record<string, { bg: string; text: string; border: string; activeBg: string }> = {
    blue: { bg: 'bg-blue-50 dark:bg-blue-950/40', text: 'text-blue-700 dark:text-blue-300', border: 'border-blue-200 dark:border-blue-800', activeBg: 'bg-blue-600 text-white border-blue-600' },
    emerald: { bg: 'bg-emerald-50 dark:bg-emerald-950/40', text: 'text-emerald-700 dark:text-emerald-300', border: 'border-emerald-200 dark:border-emerald-800', activeBg: 'bg-emerald-600 text-white border-emerald-600' },
    purple: { bg: 'bg-purple-50 dark:bg-purple-950/40', text: 'text-purple-700 dark:text-purple-300', border: 'border-purple-200 dark:border-purple-800', activeBg: 'bg-purple-600 text-white border-purple-600' },
    amber: { bg: 'bg-amber-50 dark:bg-amber-950/40', text: 'text-amber-700 dark:text-amber-300', border: 'border-amber-200 dark:border-amber-800', activeBg: 'bg-amber-600 text-white border-amber-600' },
    rose: { bg: 'bg-rose-50 dark:bg-rose-950/40', text: 'text-rose-700 dark:text-rose-300', border: 'border-rose-200 dark:border-rose-800', activeBg: 'bg-rose-600 text-white border-rose-600' },
    cyan: { bg: 'bg-cyan-50 dark:bg-cyan-950/40', text: 'text-cyan-700 dark:text-cyan-300', border: 'border-cyan-200 dark:border-cyan-800', activeBg: 'bg-cyan-600 text-white border-cyan-600' },
    indigo: { bg: 'bg-indigo-50 dark:bg-indigo-950/40', text: 'text-indigo-700 dark:text-indigo-300', border: 'border-indigo-200 dark:border-indigo-800', activeBg: 'bg-indigo-600 text-white border-indigo-600' },
  }
  return map[color] || map.blue
}


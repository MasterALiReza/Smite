import React from 'react'
import {
  RadioTower, Gamepad2, Network, Server, Radio, Dices, Key, Zap, Settings2, X,
  Activity, ShieldCheck
} from 'lucide-react'
import { CustomSelect } from '../../components/CustomSelect'
import { parseAddressPort } from '../../utils/addressUtils'
import {
  BackhaulTransport,
  BackhaulFormState,
  BackhaulAdvancedState,
  BackhaulAdvancedServerState,
  BackhaulAdvancedClientState,
  generateRandomControlPort,
  createDefaultBackhaulState,
  createDefaultBackhaulAdvancedState,
  numericServerKeys,
  booleanServerKeys,
  stringServerKeys,
  numericClientKeys,
  booleanClientKeys,
  stringClientKeys,
} from './types'

export const BACKHAUL_TRANSPORTS: BackhaulTransport[] = ['tcp', 'udp', 'ws', 'wsmux', 'tcpmux', 'wss', 'wssmux']

export function BackhaulForm({
  state,
  onChange,
  onOpenAdvanced,
  acceptUdpVisible,
}: {
  state: BackhaulFormState
  onChange: (partial: Partial<BackhaulFormState>) => void
  onOpenAdvanced: () => void
  acceptUdpVisible?: boolean
}) {
  return (
    <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-emerald-50/60 via-gray-50 to-teal-50/40 dark:from-emerald-950/20 dark:via-gray-800/60 dark:to-teal-950/20 border border-emerald-200/70 dark:border-emerald-900/40 space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
        <div className="flex items-center gap-2">
          <span className="p-1.5 rounded-lg bg-emerald-100 dark:bg-emerald-900/60 text-emerald-600 dark:text-emerald-300">
            <RadioTower size={16} />
          </span>
          <div>
            <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">
              Backhaul Settings
            </h4>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              High-Throughput Multiplexed Tunneling Core (v0.7.2)
            </p>
          </div>
        </div>
        <span className="self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium bg-emerald-100/80 text-emerald-700 dark:bg-emerald-900/60 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800">
          Backhaul Core
        </span>
      </div>

      {/* ⚡ Ultra-Low Latency Gaming Mode Card */}
      <label className="p-3.5 rounded-xl border border-indigo-200 dark:border-indigo-800/60 bg-gradient-to-r from-indigo-50/90 via-purple-50/40 to-pink-50/30 dark:from-indigo-950/40 dark:via-purple-950/30 dark:to-pink-950/20 flex items-center justify-between cursor-pointer hover:border-indigo-300 dark:hover:border-indigo-700 transition-all shadow-sm">
        <div className="pr-3">
          <div className="flex items-center gap-2">
            <span className="p-1 rounded-md bg-indigo-100 dark:bg-indigo-900/70 text-indigo-600 dark:text-indigo-400">
              <Gamepad2 size={16} />
            </span>
            <span className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
              ⚡ Ultra-Low Latency Gaming Mode
              <span className="text-[10px] uppercase font-mono font-bold tracking-wider px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 dark:bg-indigo-900/80 dark:text-indigo-300">
                Anti-Jitter
              </span>
            </span>
          </div>
          <p className="text-[11px] text-gray-600 dark:text-gray-300 mt-1 leading-normal">
            Optimizes real-time streams to eliminate jitter, packet loss, and ping spikes for competitive gaming.
          </p>
        </div>
        <input
          type="checkbox"
          className="sr-only peer"
          checked={Boolean(state.gaming_mode)}
          onChange={() => onChange({ gaming_mode: !state.gaming_mode })}
        />
        <div className="w-10 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-indigo-600 shrink-0 relative"></div>
      </label>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 items-start">
        <div>
          <div className="flex items-center justify-between mb-1.5 h-5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <Network size={14} className="text-blue-500" />
              Transport
            </label>
          </div>
          <CustomSelect
            value={state.transport}
            onChange={(val) => onChange({ transport: val as BackhaulTransport })}
            options={[
              { value: 'tcpmux', label: 'TCPMux (SMUX - Recommended)' },
              { value: 'tcp', label: 'TCP (Direct Stream)' },
              { value: 'ws', label: 'WebSocket (WS)' },
              { value: 'wsmux', label: 'WS Mux' },
              { value: 'wss', label: 'WSS (TLS Secure)' },
              { value: 'wssmux', label: 'WSS Mux (TLS)' },
            ]}
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
            Underlying connection protocol between nodes.
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5 h-5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <Server size={14} className="text-emerald-500" />
              Control Port
            </label>
            <button
              type="button"
              onClick={() => onChange({ control_port: generateRandomControlPort() })}
              className="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-medium bg-emerald-50 dark:bg-emerald-950/40 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-100 dark:hover:bg-emerald-900/60 border border-emerald-200/60 dark:border-emerald-800/40 transition-all cursor-pointer shadow-xs"
              title="Generate Random Control Port"
            >
              <Dices size={12} />
              <span>Random</span>
            </button>
          </div>
          <input
            type="number"
            value={state.control_port}
            onChange={(e) => onChange({ control_port: e.target.value })}
            className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 transition-all font-mono"
            placeholder="3080"
            min={1}
            max={65535}
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
            Port connecting to Iran server.
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5 h-5">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <Radio size={14} className="text-teal-500" />
              Forwarded Ports
            </label>
            <span className="text-[11px] text-gray-400">Public & Target</span>
          </div>
          <input
            type="text"
            value={state.public_port}
            onChange={(e) => {
              onChange({ public_port: e.target.value, target_port: e.target.value })
            }}
            className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 transition-all font-mono"
            placeholder="8080,8081 or 27000-27050"
          />
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
            Ports (8080) or ranges (27000-27050).
          </p>
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
            <Key size={14} className="text-indigo-500" />
            Authentication Token
          </label>
          <span className="text-[11px] text-gray-400">Optional</span>
        </div>
        <input
          type="text"
          value={state.token}
          onChange={(e) => onChange({ token: e.target.value })}
          className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all font-mono"
          placeholder="Leave empty for auto-generation"
        />
        <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
          Mutual auth token (auto-generated if empty).
        </p>
      </div>

      {acceptUdpVisible && (
        <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
          <div className="pr-2">
            <div className="flex items-center gap-1.5">
              <Zap size={15} className="text-emerald-600 dark:text-emerald-400" />
              <span className="text-xs font-bold text-gray-900 dark:text-white">Allow UDP over TCP (Turbo)</span>
            </div>
            <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
              Encapsulate UDP packets inside TCP streams for networks with strict UDP filtering
            </p>
          </div>
          <input
            type="checkbox"
            className="sr-only peer"
            checked={state.accept_udp}
            onChange={() => onChange({ accept_udp: !state.accept_udp })}
          />
          <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-emerald-600 shrink-0 relative"></div>
        </label>
      )}

      <div className="pt-1 flex items-center justify-end">
        <button
          type="button"
          onClick={onOpenAdvanced}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-emerald-700 dark:text-emerald-300 bg-emerald-100/70 hover:bg-emerald-200/70 dark:bg-emerald-950/40 dark:hover:bg-emerald-900/60 rounded-lg border border-emerald-300 dark:border-emerald-800 transition-colors"
        >
          <Settings2 size={13} />
          Advanced Engine Parameters
        </button>
      </div>
    </div>
  )
}

export function BackhaulAdvancedDrawer({
  open,
  onClose,
  state,
  onChange,
}: {
  open: boolean
  onClose: () => void
  state: BackhaulAdvancedState
  onChange: (next: BackhaulAdvancedState) => void
}) {
  if (!open) {
    return null
  }

  const updateServer = (key: keyof BackhaulAdvancedServerState, value: string | boolean) => {
    onChange({
      ...state,
      server: {
        ...state.server,
        [key]: value,
      },
    })
  }

  const updateClient = (key: keyof BackhaulAdvancedClientState, value: string | boolean) => {
    onChange({
      ...state,
      client: {
        ...state.client,
        [key]: value,
      },
    })
  }

  const updateBoth = (key: string, value: string | boolean) => {
    onChange({
      ...state,
      server: {
        ...state.server,
        [key]: value,
      },
      client: {
        ...state.client,
        [key]: value,
      },
    })
  }

  return (
    <div className="fixed inset-0 z-[100] flex">
      <div className="flex-1 bg-black/50 backdrop-blur-sm transition-opacity" onClick={onClose} />
      <div className="w-full max-w-2xl h-full bg-white dark:bg-gray-900 shadow-2xl overflow-y-auto p-6 border-l border-gray-200 dark:border-gray-800">
        <div className="flex justify-between items-center pb-4 mb-6 border-b border-gray-200 dark:border-gray-800">
          <div className="flex items-center gap-2.5">
            <span className="p-2 rounded-xl bg-emerald-100 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-400">
              <Settings2 size={20} />
            </span>
            <div>
              <h3 className="text-lg font-bold text-gray-900 dark:text-white">Backhaul Engine Tuning</h3>
              <p className="text-xs text-gray-500 dark:text-gray-400">Upstream Musixal/Backhaul v0.7.2 High-Performance Core</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        <div className="space-y-6">
          {/* Card 1: ⚡ Low-Latency & Gaming Tuning */}
          <div className="p-4 rounded-xl border border-indigo-200/80 dark:border-indigo-900/50 bg-indigo-50/30 dark:bg-indigo-950/20 space-y-4">
            <div className="flex items-center gap-2 text-indigo-700 dark:text-indigo-300 font-bold text-xs uppercase tracking-wider">
              <Zap size={15} />
              <span>Latency & WAN Transmission (Gaming & Real-time)</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
              <label className="p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 flex items-center justify-between cursor-pointer">
                <div>
                  <span className="text-xs font-semibold text-gray-800 dark:text-gray-200 block">Server TCP Nodelay</span>
                  <span className="text-[10px] text-gray-500 dark:text-gray-400">Disables Nagle buffering</span>
                </div>
                <input
                  type="checkbox"
                  className="rounded text-indigo-600 focus:ring-indigo-500"
                  checked={state.server.nodelay}
                  onChange={(e) => updateServer('nodelay', e.target.checked)}
                />
              </label>

              <label className="p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 flex items-center justify-between cursor-pointer">
                <div>
                  <span className="text-xs font-semibold text-gray-800 dark:text-gray-200 block">Client TCP Nodelay</span>
                  <span className="text-[10px] text-gray-500 dark:text-gray-400">Instant client tick dispatch</span>
                </div>
                <input
                  type="checkbox"
                  className="rounded text-indigo-600 focus:ring-indigo-500"
                  checked={state.client.nodelay}
                  onChange={(e) => updateClient('nodelay', e.target.checked)}
                />
              </label>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  MSS Clamping (MTU Optimization)
                </label>
                <input
                  type="number"
                  value={state.server.mss}
                  onChange={(e) => updateBoth('mss', e.target.value)}
                  placeholder="e.g. 1380 (prevents fragmentation)"
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={1000}
                  max={1500}
                />
                <span className="text-[10px] text-gray-400">Recommended 1360-1400 for WAN gaming</span>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Heartbeat & Keepalive (seconds)
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="number"
                    value={state.server.heartbeat}
                    onChange={(e) => updateBoth('heartbeat', e.target.value)}
                    placeholder="Heartbeat (12)"
                    className="w-full px-2.5 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                    min={1}
                    max={25}
                  />
                  <input
                    type="number"
                    value={state.server.keepalive_period}
                    onChange={(e) => updateBoth('keepalive_period', e.target.value)}
                    placeholder="Keepalive (12)"
                    className="w-full px-2.5 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                    min={1}
                    max={25}
                  />
                </div>
                <span className="text-[10px] text-gray-400">Fast link loss detection (≤ 25s)</span>
              </div>

              <label className="p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 flex items-center justify-between cursor-pointer col-span-1 sm:col-span-2">
                <div>
                  <span className="text-xs font-semibold text-gray-800 dark:text-gray-200 block">Skip Kernel Sysctl Optimizations (Docker Mode)</span>
                  <span className="text-[10px] text-gray-500 dark:text-gray-400">Bypasses sysctl error logs in unprivileged Docker containers</span>
                </div>
                <input
                  type="checkbox"
                  className="rounded text-indigo-600 focus:ring-indigo-500"
                  checked={Boolean(state.server.skip_optz || state.client.skip_optz)}
                  onChange={(e) => updateBoth('skip_optz', e.target.checked)}
                />
              </label>
            </div>
          </div>

          {/* Card 2: 🚀 Buffer & Socket Capacity */}
          <div className="p-4 rounded-xl border border-teal-200/80 dark:border-teal-900/50 bg-teal-50/30 dark:bg-teal-950/20 space-y-4">
            <div className="flex items-center gap-2 text-teal-700 dark:text-teal-300 font-bold text-xs uppercase tracking-wider">
              <Activity size={15} />
              <span>Socket & Buffer Capacity</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Channel Queue Size
                </label>
                <input
                  type="number"
                  value={state.server.channel_size}
                  onChange={(e) => updateBoth('channel_size', e.target.value)}
                  placeholder="2048 (default) or 8192 (gaming)"
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={512}
                />
                <span className="text-[10px] text-gray-400">Queue buffer for high-frequency game bursts</span>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Client Connection Pool
                </label>
                <input
                  type="number"
                  value={state.client.connection_pool}
                  onChange={(e) => updateClient('connection_pool', e.target.value)}
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={1}
                />
                <span className="text-[10px] text-gray-400">Parallel multiplex connections (default 8)</span>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Socket RCV Buffer (so_rcvbuf bytes)
                </label>
                <input
                  type="number"
                  value={state.server.so_rcvbuf}
                  onChange={(e) => updateBoth('so_rcvbuf', e.target.value)}
                  placeholder="e.g. 2097152 (2MB)"
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Socket SND Buffer (so_sndbuf bytes)
                </label>
                <input
                  type="number"
                  value={state.server.so_sndbuf}
                  onChange={(e) => updateBoth('so_sndbuf', e.target.value)}
                  placeholder="e.g. 2097152 (2MB)"
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                />
              </div>

              <label className="p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 flex items-center justify-between cursor-pointer col-span-1 sm:col-span-2">
                <div>
                  <span className="text-xs font-semibold text-gray-800 dark:text-gray-200 block">Aggressive Pool</span>
                  <span className="text-[10px] text-gray-500 dark:text-gray-400">Maintain pooled connections in hot standby state</span>
                </div>
                <input
                  type="checkbox"
                  className="rounded text-teal-600 focus:ring-teal-500"
                  checked={state.client.aggressive_pool}
                  onChange={(e) => updateClient('aggressive_pool', e.target.checked)}
                />
              </label>
            </div>
          </div>

          {/* Card 3: 🔀 SMUX Multiplexing Engine */}
          <div className="p-4 rounded-xl border border-cyan-200/80 dark:border-cyan-900/50 bg-cyan-50/30 dark:bg-cyan-950/20 space-y-4">
            <div className="flex items-center gap-2 text-cyan-700 dark:text-cyan-300 font-bold text-xs uppercase tracking-wider">
              <Network size={15} />
              <span>SMUX Multiplexing Engine</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Yamux Protocol Version
                </label>
                <CustomSelect
                  value={state.server.mux_version || '1'}
                  onChange={(val) => updateBoth('mux_version', val)}
                  options={[
                    { value: '1', label: 'Version 1 (Standard)' },
                    { value: '2', label: 'Version 2 (Modern)' },
                  ]}
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Mux Frame Size (bytes)
                </label>
                <input
                  type="number"
                  value={state.server.mux_framesize}
                  onChange={(e) => updateBoth('mux_framesize', e.target.value)}
                  placeholder="32768 (default) or 4096 (gaming)"
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                />
                <span className="text-[10px] text-gray-400">4096 bytes dispatches game ticks instantly</span>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Mux Concurrency
                </label>
                <input
                  type="number"
                  value={state.server.mux_con}
                  onChange={(e) => updateServer('mux_con', e.target.value)}
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  min={1}
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Stream Buffer (bytes)
                </label>
                <input
                  type="number"
                  value={state.server.mux_streambuffer}
                  onChange={(e) => updateBoth('mux_streambuffer', e.target.value)}
                  placeholder="e.g. 131072 (128KB)"
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                />
              </div>
            </div>
          </div>

          {/* Card 4: 🛡️ Security, Proxy & Monitoring */}
          <div className="p-4 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/40 space-y-4">
            <div className="flex items-center gap-2 text-gray-700 dark:text-gray-300 font-bold text-xs uppercase tracking-wider">
              <ShieldCheck size={15} />
              <span>Security, Proxy & Port Ranges</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
              <label className="p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 flex items-center justify-between cursor-pointer col-span-1 sm:col-span-2">
                <div>
                  <span className="text-xs font-semibold text-gray-800 dark:text-gray-200 block">HAProxy Proxy Protocol</span>
                  <span className="text-[10px] text-gray-500 dark:text-gray-400">Preserves original client IP addresses across reverse proxies</span>
                </div>
                <input
                  type="checkbox"
                  className="rounded text-blue-600 focus:ring-blue-500"
                  checked={state.server.proxy_protocol}
                  onChange={(e) => updateServer('proxy_protocol', e.target.checked)}
                />
              </label>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Log Level</label>
                <CustomSelect
                  value={state.server.log_level}
                  onChange={(val) => updateBoth('log_level', val)}
                  options={[
                    { value: 'info', label: 'Info' },
                    { value: 'warn', label: 'Warn' },
                    { value: 'error', label: 'Error' },
                    { value: 'debug', label: 'Debug' },
                    { value: 'trace', label: 'Trace' },
                  ]}
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Web Monitor Port</label>
                <input
                  type="number"
                  value={state.server.web_port}
                  onChange={(e) => updateServer('web_port', e.target.value)}
                  className="w-full px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  placeholder="0 (disabled)"
                  min={0}
                />
              </div>

              <div className="col-span-1 sm:col-span-2">
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Custom Port Mappings & Ranges
                </label>
                <textarea
                  value={state.customPorts}
                  onChange={(e) => onChange({ ...state, customPorts: e.target.value })}
                  className="w-full min-h-[90px] px-3 py-2 text-xs font-mono border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-800 dark:text-white"
                  placeholder={`One entry per line. Examples:\n443\n443=127.0.0.1:8080\n27000-27050=127.0.0.1:27000-27050`}
                />
                <span className="text-[10px] text-gray-400">Leave empty to use single port from main form</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export function buildBackhaulSpec(
  base: BackhaulFormState,
  advanced: BackhaulAdvancedState,
  transportOverride?: BackhaulTransport,
): Record<string, any> {
  const transport = transportOverride ?? base.transport
  const isPureUdp = transport === 'udp'
  const isUdpOverTcp = isPureUdp || Boolean(base.accept_udp)
  const normalizedTransport = isPureUdp ? 'tcp' : transport
  const controlPort = parseInt(base.control_port, 10)
  const publicPort = parseInt(base.public_port, 10)
  const targetPort = parseInt(base.target_port, 10)
  const listenIp = base.listen_ip.trim() || '0.0.0.0'
  const targetHost = base.target_host.trim() || '127.0.0.1'
  const token = base.token.trim()
  const panelHost = base.public_host.trim() || (typeof window !== 'undefined' ? window.location.hostname : '') || '127.0.0.1'

  const effectiveControlPort = !Number.isNaN(controlPort) && controlPort > 0
    ? controlPort
    : (!Number.isNaN(publicPort) && publicPort > 0
        ? (publicPort + 10000 > 65535 ? publicPort - 10000 : publicPort + 10000)
        : (!Number.isNaN(targetPort) && targetPort > 0 ? targetPort : 3080))
  
  // Parse comma-separated ports or ranges from public_port without truncating ranges
  const parsePortsFromString = (portStr: string): string[] => {
    if (!portStr || typeof portStr !== 'string') {
      return []
    }
    const parsed = portStr
      .split(',')
      .map(p => p.trim())
      .filter(p => {
        if (!p) return false
        if (p.includes('-')) {
          const parts = p.split('-').map(x => parseInt(x.trim(), 10))
          return parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1]) && parts[0] > 0 && parts[1] <= 65535 && parts[0] <= parts[1]
        }
        const num = parseInt(p, 10)
        return !isNaN(num) && num > 0 && num <= 65535
      })
    return parsed
  }
  
  const publicPortStr = String(base.public_port || '')
  const publicPorts = parsePortsFromString(publicPortStr)
  const firstPortRaw = publicPorts.length > 0 ? publicPorts[0] : ''
  const firstPortNum = firstPortRaw.includes('-')
    ? parseInt(firstPortRaw.split('-')[0], 10)
    : parseInt(firstPortRaw, 10)
  const effectivePublicPort = !Number.isNaN(firstPortNum) && firstPortNum > 0
    ? firstPortNum
    : (!Number.isNaN(publicPort) && publicPort > 0 ? publicPort : 8080)
  const effectiveTargetPort = !Number.isNaN(targetPort) && targetPort > 0 ? targetPort : effectivePublicPort

  const remoteAddr = base.remote_addr.trim() || `${panelHost}:${effectiveControlPort}`
  const listenedPort = listenIp !== '0.0.0.0' ? `${listenIp}:${effectivePublicPort}` : `${effectivePublicPort}`
  const defaultPortEntry = `${listenedPort}=${targetHost}:${effectiveTargetPort}`

  let ports: string[] = []
  const hasCustomPorts = advanced.customPorts && advanced.customPorts.trim().length > 0
  
  if (hasCustomPorts) {
    ports = advanced.customPorts
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
  } else if (publicPorts.length > 0) {
    ports = publicPorts.map(p => {
      const listenedPort = listenIp !== '0.0.0.0' ? `${listenIp}:${p}` : `${p}`
      return `${listenedPort}=${targetHost}:${p}`
    })
  }
  
  if (ports.length === 0) {
    ports.push(defaultPortEntry)
  }

  const serverOptions: Record<string, any> = {}
  Object.entries(advanced.server).forEach(([key, value]) => {
    if (booleanServerKeys.has(key)) {
      if (value) {
        serverOptions[key] = true
      }
      return
    }
    if (numericServerKeys.has(key)) {
      const num = Number(value)
      if (!Number.isNaN(num) && value !== '') {
        serverOptions[key] = num
      }
      return
    }
    if (stringServerKeys.has(key)) {
      const val = typeof value === 'string' ? value.trim() : value
      if (val) {
        serverOptions[key] = val
      }
    }
  })

  const clientOptions: Record<string, any> = {}
  Object.entries(advanced.client).forEach(([key, value]) => {
    if (booleanClientKeys.has(key)) {
      if (value) {
        clientOptions[key] = true
      }
      return
    }
    if (numericClientKeys.has(key)) {
      const num = Number(value)
      if (!Number.isNaN(num) && value !== '') {
        clientOptions[key] = num
      }
      return
    }
    if (stringClientKeys.has(key)) {
      const val = typeof value === 'string' ? value.trim() : value
      if (val) {
        clientOptions[key] = val
      }
    }
  })

  if (isUdpOverTcp) {
    serverOptions.accept_udp = true
    clientOptions.accept_udp = true
  }
  if (!serverOptions.keepalive_period || Number(serverOptions.keepalive_period) > 25) {
    serverOptions.keepalive_period = base.gaming_mode ? 12 : 20
  }
  if (!serverOptions.heartbeat || Number(serverOptions.heartbeat) > 25) {
    serverOptions.heartbeat = base.gaming_mode ? 12 : 20
  }
  if (!clientOptions.keepalive_period || Number(clientOptions.keepalive_period) > 25) {
    clientOptions.keepalive_period = base.gaming_mode ? 12 : 20
  }
  if (!clientOptions.heartbeat || Number(clientOptions.heartbeat) > 25) {
    clientOptions.heartbeat = base.gaming_mode ? 12 : 20
  }

  const spec: Record<string, any> = {
    transport: normalizedTransport,
    bind_addr: `0.0.0.0:${effectiveControlPort}`,
    remote_addr: remoteAddr,
    listen_ip: listenIp,
    control_port: effectiveControlPort,
    public_port: effectivePublicPort,
    listen_port: effectivePublicPort,
    target_host: targetHost,
    target_port: effectiveTargetPort,
    target_addr: `${targetHost}:${effectiveTargetPort}`,
    public_host: panelHost,
    ports,
  }

  if (token) {
    spec.token = token
  }
  if (isUdpOverTcp) {
    spec.accept_udp = true
  }
  if (base.gaming_mode) {
    spec.gaming_mode = true
  }
  if (Object.keys(serverOptions).length > 0) {
    spec.server_options = serverOptions
  }
  if (Object.keys(clientOptions).length > 0) {
    spec.client_options = clientOptions
  }

  return spec
}

export function parseBackhaulSpec(spec: Record<string, any>, currentType: string): {
  state: BackhaulFormState
  advanced: BackhaulAdvancedState
} {
  const state = createDefaultBackhaulState()
  const advanced = createDefaultBackhaulAdvancedState()

  const candidateTransport = (spec?.transport || spec?.transport_type || (currentType !== 'tcp' && currentType !== 'udp' && currentType !== 'tcp+udp' ? currentType : '') || 'tcpmux') as BackhaulTransport
  if (BACKHAUL_TRANSPORTS.includes(candidateTransport)) {
    state.transport = candidateTransport === 'udp' ? 'tcpmux' : candidateTransport
  }
  if (spec?.accept_udp === true || currentType === 'udp' || currentType === 'tcp+udp') {
    state.accept_udp = true
  }

  if (!spec) {
    return { state, advanced }
  }

  const controlPortCandidate =
    spec.control_port ??
    extractPort(spec.bind_addr) ??
    extractPort(spec.remote_addr)
  if (controlPortCandidate) {
    state.control_port = String(controlPortCandidate)
  }

  state.listen_ip = spec.listen_ip ?? state.listen_ip

  if (Array.isArray(spec.ports) && spec.ports.length > 0) {
    const extracted = spec.ports.map(p => {
      if (typeof p === 'string') {
        const left = p.includes('=') ? p.split('=')[0].trim() : p.trim()
        return left.includes(':') ? left.split(':')[1] : left
      }
      return String(p)
    }).filter(Boolean)
    if (extracted.length > 0) {
      state.public_port = extracted.join(',')
      state.target_port = extracted.join(',')
    }
  } else {
    const publicPortCandidate =
      spec.public_port ??
      spec.listen_port ??
      derivePortFromPorts(spec.ports)
    if (publicPortCandidate) {
      state.public_port = String(publicPortCandidate)
    }
  }

  if (spec.target_host) {
    state.target_host = String(spec.target_host)
  } else if (typeof spec.target_addr === 'string') {
    const parsed = parseAddressPort(spec.target_addr)
    state.target_host = parsed.host
  }

  const targetPortCandidate =
    spec.target_port ??
    (typeof spec.target_addr === 'string'
      ? parseAddressPort(spec.target_addr).port
      : undefined)
  if (targetPortCandidate) {
    state.target_port = String(targetPortCandidate)
  }

  state.token = spec.token ?? ''
  state.public_host = spec.public_host ?? ''
  state.remote_addr = spec.remote_addr ?? ''
  state.accept_udp = Boolean(spec.accept_udp)
  if (spec.gaming_mode !== undefined) {
    state.gaming_mode = Boolean(spec.gaming_mode)
  }

  if (Array.isArray(spec.ports) && spec.ports.length > 0) {
    advanced.customPorts = spec.ports.join('\n')
  }

  const serverOptions = spec.server_options || {}
  Object.entries(advanced.server).forEach(([key, defaultValue]) => {
    const value = serverOptions[key]
    if (value === undefined || value === null) {
      return
    }
    if (typeof defaultValue === 'boolean') {
      (advanced.server as any)[key] = Boolean(value)
    } else {
      (advanced.server as any)[key] = String(value)
    }
  })

  const clientOptions = spec.client_options || {}
  Object.entries(advanced.client).forEach(([key, defaultValue]) => {
    const value = clientOptions[key]
    if (value === undefined || value === null) {
      return
    }
    if (typeof defaultValue === 'boolean') {
      (advanced.client as any)[key] = Boolean(value)
    } else {
      (advanced.client as any)[key] = String(value)
    }
  })

  return { state, advanced }
}

export function extractPort(value: unknown): string | undefined {
  if (typeof value === 'number') {
    return value.toString()
  }
  if (typeof value === 'string') {
    const parts = value.split(':')
    const port = parts[parts.length - 1]
    if (port && !Number.isNaN(Number(port))) {
      return port
    }
  }
  return undefined
}

export function derivePortFromPorts(value: unknown): string | undefined {
  if (!Array.isArray(value) || value.length === 0) {
    return undefined
  }
  const first = value[0]
  if (typeof first !== 'string') {
    return undefined
  }
  const [left] = first.split('=')
  if (!left) {
    return undefined
  }
  const segments = left.split(':')
  const port = segments[segments.length - 1]
  return port && !Number.isNaN(Number(port)) ? port : undefined
}

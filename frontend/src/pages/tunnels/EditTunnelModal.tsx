import React, { useState, useEffect } from 'react'
import { 
  X, Loader2, AlertTriangle, ShieldCheck, Gamepad2, Settings2, Sliders, Globe,
  Network, Cpu, Radio, Key, Lock, EyeOff, Gauge, Server, Terminal, RefreshCw, Zap,
  Dices, ArrowLeftRight, Edit2, Tag, FolderMinus, Sparkles, Shield, Layers, Rocket,
  Activity, Fingerprint, Scale, CheckCircle2, XCircle
} from 'lucide-react'
import api from '../../api/client'
import { parseAddressPort, formatAddressPort } from '../../utils/addressUtils'
import { useLanguage } from '../../contexts/LanguageContext'
import { useToast } from '../../contexts/ToastContext'
import { CustomSelect } from '../../components/CustomSelect'
import { LatencyBadge } from '../../components/LatencyBadge'
import {
  BackhaulForm, BackhaulAdvancedDrawer, buildBackhaulSpec, parseBackhaulSpec
} from './BackhaulComponents'
import {
  Tunnel, TunnelCategory, BackhaulTransport, BackhaulFormState,
  BackhaulAdvancedState, BackhaulAdvancedServerState, BackhaulAdvancedClientState,
  generateRandomControlPort, createDefaultBackhaulState, createDefaultBackhaulAdvancedState,
  numericServerKeys, booleanServerKeys, stringServerKeys,
  numericClientKeys, booleanClientKeys, stringClientKeys, getCategoryColorClasses
} from './types'

interface EditTunnelModalProps {
  tunnel: Tunnel
  nodes: any[]
  categories?: TunnelCategory[]
  onCategoryCreated?: (name: string, color?: string) => Promise<any>
  onClose: () => void
  onSuccess: () => void
}

const EditTunnelModal = ({ tunnel, nodes, categories = [], onCategoryCreated, onClose, onSuccess }: EditTunnelModalProps) => {
  const { t } = useLanguage()
  const { showToast } = useToast()
  const forwardToParsed = tunnel.spec?.forward_to ? parseAddressPort(tunnel.spec.forward_to) : null
  const remoteIp = tunnel.spec?.remote_ip || forwardToParsed?.host || '127.0.0.1'
  const remotePort = tunnel.spec?.remote_port || forwardToParsed?.port || 8080
  
  // Parse ports from spec
  const parsePortsFromSpec = (spec: Record<string, any>): string => {
    if (spec?.ports) {
      if (Array.isArray(spec.ports)) {
        // For Backhaul, ports are in format "8080=127.0.0.1:8080" or "0.0.0.0:8080=127.0.0.1:8080"
        // Extract just the port number (first number before = or after :)
        return spec.ports.map(p => {
          if (typeof p === 'object' && p.local) {
            return p.local.toString()
          } else if (typeof p === 'string') {
            // Handle Backhaul format: "8080=127.0.0.1:8080" or "0.0.0.0:8080=127.0.0.1:8080"
            if (p.includes('=')) {
              const leftPart = p.split('=')[0]
              // Extract port from left part (could be "8080" or "0.0.0.0:8080")
              if (leftPart.includes(':')) {
                return leftPart.split(':')[1]
              }
              return leftPart
            }
            // If it's just a number, return as-is
            return p
          }
          return p.toString()
        }).join(',')
      } else if (typeof spec.ports === 'string') {
        return spec.ports
      }
    }
    // Fallback to single port
    return (spec?.listen_port || spec?.remote_port || 8080).toString()
  }
  
  const [formData, setFormData] = useState({
    name: tunnel.name,
    type: tunnel.type || 'tcp',
    node_id: tunnel.node_id || '',
    iran_node_id: tunnel.iran_node_id || tunnel.node_id || '',
    foreign_node_id: tunnel.foreign_node_id || '',
    ports: (() => {
      const basePorts = parsePortsFromSpec(tunnel.spec || {})
      const ranges = tunnel.port_ranges || tunnel.spec?.port_ranges
      if (ranges && Array.isArray(ranges) && ranges.length > 0) {
        return basePorts ? `${basePorts},${ranges.join(',')}` : ranges.join(',')
      }
      return basePorts
    })(),
    remote_ip: remoteIp,
    rathole_remote_addr: tunnel.spec?.control_port ? tunnel.spec.control_port.toString() : (tunnel.spec?.remote_addr ? (() => {
      const parsed = parseAddressPort(tunnel.spec.remote_addr)
      return parsed.port?.toString() || ''
    })() : ''),
    rathole_token: tunnel.spec?.token || tunnel.spec?.rathole_token || '',
    rathole_transport: tunnel.spec?.transport_type || tunnel.spec?.transport || 'tcp',
    rathole_local_port: tunnel.spec?.local_port ? tunnel.spec.local_port.toString() : '8080',
    chisel_control_port: tunnel.spec?.control_port ? tunnel.spec.control_port.toString() : '',
    chisel_transport: tunnel.spec?.transport || tunnel.spec?.transport_type || 'ws',
    chisel_backend_url: tunnel.spec?.backend_url || '',
    chisel_custom_sni: tunnel.spec?.custom_sni || tunnel.spec?.stealth_domain || '',
    chisel_custom_host: tunnel.spec?.custom_host || '',
    chisel_keepalive: tunnel.spec?.keepalive || '10s',
    frp_bind_port: tunnel.spec?.bind_port ? tunnel.spec.bind_port.toString() : '7000',
    frp_token: tunnel.spec?.token || '',
    frp_local_ip: tunnel.spec?.local_ip || '127.0.0.1',
    frp_transport: tunnel.spec?.transport_type || tunnel.spec?.transport || 'tcp',
    frp_security: tunnel.spec?.security_type || 'tls',
    frp_sni: tunnel.spec?.custom_sni || tunnel.spec?.stealth_domain || '',
    frp_encryption: tunnel.spec?.use_encryption !== false,
    frp_compression: tunnel.spec?.use_compression !== false,
    frp_health_check: tunnel.spec?.enable_health_check !== false && tunnel.spec?.health_check_type !== null,
    frp_bandwidth_limit: tunnel.spec?.bandwidth_limit || '',
    frp_proxy_protocol: tunnel.spec?.proxy_protocol_version || 'none',
    frp_custom_domains: Array.isArray(tunnel.spec?.custom_domains) ? tunnel.spec.custom_domains.join(', ') : (tunnel.spec?.custom_domains || ''),
    node_ipv6: tunnel.spec?.node_ipv6 || '',
    cdn_mode: tunnel.cdn_mode || false,
    gaming_mode: tunnel.gaming_mode || false,
    custom_host: tunnel.custom_host || '',
    custom_sni: tunnel.custom_sni || tunnel.spec?.custom_sni || tunnel.spec?.stealth_domain || '',
    ws_path: tunnel.ws_path || '',
    is_reverse: tunnel.is_reverse !== undefined && tunnel.is_reverse !== null
      ? Boolean(tunnel.is_reverse)
      : (tunnel.spec?.is_reverse !== undefined && tunnel.spec?.is_reverse !== null
          ? Boolean(tunnel.spec.is_reverse)
          : !tunnel.spec?.force_direct),
    stealth_domain: tunnel.stealth_domain || tunnel.spec?.stealth_domain || tunnel.spec?.custom_sni || '',
    transport_type: tunnel.transport_type || 'tcp',
    security_type: tunnel.security_type || 'none',
    selector_strategy: tunnel.selector_strategy || 'fifo',
    utls_fingerprint: tunnel.utls_fingerprint || 'chrome',
    keepalive_interval: tunnel.keepalive_interval || 15,
    failover_ips: tunnel.failover_ips && Array.isArray(tunnel.failover_ips) ? tunnel.failover_ips.join('\n') : '',
    rate_limit_mbps: tunnel.rate_limit_mbps ? tunnel.rate_limit_mbps.toString() : '',
    allowed_ips: tunnel.allowed_ips && Array.isArray(tunnel.allowed_ips) ? tunnel.allowed_ips.join('\n') : '',
    rate_limit_enabled: !!tunnel.rate_limit_mbps,
    allowed_ips_enabled: !!(tunnel.allowed_ips && tunnel.allowed_ips.length > 0),
    category: tunnel.category || '',
  })
  const [showInlineNewCategory, setShowInlineNewCategory] = useState(false)
  const [inlineCategoryName, setInlineCategoryName] = useState('')
  const parsedBackhaul = parseBackhaulSpec(tunnel.spec, tunnel.type)
  const [backhaulState, setBackhaulState] = useState<BackhaulFormState>(parsedBackhaul.state)
  const [backhaulAdvanced, setBackhaulAdvanced] = useState<BackhaulAdvancedState>(parsedBackhaul.advanced)
  const [showBackhaulAdvanced, setShowBackhaulAdvanced] = useState(false)
  const [isTestingConfig, setIsTestingConfig] = useState(false)
  const [testResult, setTestResult] = useState<any | null>(null)

  const handleTestConfig = async () => {
    setIsTestingConfig(true)
    setTestResult(null)
    try {
      const payload: any = {
        core: tunnel.core,
        iran_node_id: tunnel.iran_node_id || tunnel.node_id,
        foreign_node_id: tunnel.foreign_node_id,
        ports: tunnel.core === 'backhaul' ? ((backhaulAdvanced as any).port_ranges ? `${backhaulState.public_port},${(backhaulAdvanced as any).port_ranges}` : backhaulState.public_port) : formData.ports,
        control_port: tunnel.core === 'backhaul' ? ((backhaulAdvanced as any).control_port || backhaulState.control_port) : (tunnel.core === 'rathole' ? formData.rathole_remote_addr : (tunnel.core === 'frp' ? formData.frp_bind_port : (tunnel.core === 'chisel' ? formData.chisel_control_port : undefined))),
        rathole_token: formData.rathole_token,
        rathole_transport: formData.rathole_transport,
        rathole_remote_addr: formData.rathole_remote_addr,
        frp_token: formData.frp_token,
        frp_transport: formData.frp_transport,
        frp_security: formData.frp_security,
        frp_health_check: formData.frp_health_check,
        frp_bandwidth_limit: formData.frp_bandwidth_limit,
        frp_proxy_protocol: formData.frp_proxy_protocol,
        chisel_transport: formData.chisel_transport,
        chisel_backend_url: formData.chisel_backend_url,
        transport: tunnel.core === 'backhaul' ? backhaulState.transport : (tunnel.core === 'rathole' ? formData.rathole_transport : (tunnel.core === 'chisel' ? formData.chisel_transport : (tunnel.core === 'frp' ? formData.frp_transport : formData.transport_type))),
        security_type: formData.security_type,
        cdn_mode: formData.cdn_mode,
        ws_path: formData.ws_path,
        keepalive_interval: formData.keepalive_interval,
        is_reverse: formData.is_reverse,
      }
      const response = await api.post('/tunnels/test-config', payload)
      setTestResult(response.data)
      if (response.data?.valid) {
        showToast('success', 'Configuration Valid', response.data.summary || 'All checks passed!')
      } else {
        showToast('warning', 'Configuration Warning', response.data.summary || 'Some checks failed')
      }
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message || 'Could not perform test'
      showToast('error', 'Test Failed', msg)
      setTestResult({
        valid: false,
        summary: msg,
        checks: [{ title: 'Connection Error', status: 'failed', detail: msg }]
      })
    } finally {
      setIsTestingConfig(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      let updatedSpec = { ...tunnel.spec }
      
      const useV4ToV6 = updatedSpec.use_ipv6 || false
      
      // Parse comma-separated ports and ranges
      const parsePortsAndRanges = (portsStr: string): { ports: number[], port_ranges: string[] } => {
        const ports: number[] = []
        const port_ranges: string[] = []
        
        portsStr.split(',').forEach(p => {
          const trimmed = p.trim()
          if (!trimmed) return
          if (trimmed.includes('-')) {
            port_ranges.push(trimmed)
          } else {
            const num = parseInt(trimmed)
            if (!isNaN(num) && num > 0 && num <= 65535) {
              ports.push(num)
            }
          }
        })
        return { ports, port_ranges }
      }
      
      let ports: number[] = []
      let port_ranges: string[] = []
      
      if (tunnel.core === 'gost') {
        const parsed = parsePortsAndRanges(formData.ports)
        ports = parsed.ports
        port_ranges = parsed.port_ranges
        if (ports.length === 0 && port_ranges.length === 0) {
          showToast('warning', 'Invalid Ports', 'Please enter at least one valid port or port range')
          return
        }
      } else if (tunnel.core === 'backhaul') {
        const rawBhPorts = backhaulState.public_port || formData.ports || '8080'
        const parsed = parsePortsAndRanges(rawBhPorts)
        ports = parsed.ports
        port_ranges = parsed.port_ranges
        if (ports.length === 0 && port_ranges.length === 0 && !backhaulAdvanced.customPorts && !(backhaulAdvanced as any).port_ranges) {
          showToast('warning', 'Invalid Ports', 'Please enter at least one valid port or port range')
          return
        }
      } else {
        ports = formData.ports
          .split(',')
          .map(p => p.trim())
          .filter(p => p)
          .map(p => parseInt(p))
          .filter(p => !isNaN(p) && p > 0 && p <= 65535)
          
        if (ports.length === 0) {
          showToast('warning', 'Invalid Port', 'Please enter at least one valid port')
          return
        }
      }
      
      if (tunnel.core === 'rathole') {
        if (formData.rathole_remote_addr) {
          const remotePort = formData.rathole_remote_addr.includes(':') 
            ? formData.rathole_remote_addr.split(':')[1] 
            : formData.rathole_remote_addr
          const parsedPort = parseInt(remotePort) || 23333
          updatedSpec.control_port = parsedPort
          const selectedIran = nodes.find(n => n.id === (formData.iran_node_id || tunnel.iran_node_id || tunnel.node_id))
          const remoteHost = selectedIran?.ip_address || selectedIran?.metadata?.ip_address || window.location.hostname
          updatedSpec.remote_addr = `${remoteHost}:${parsedPort}`
        }
        if (formData.rathole_token) {
          updatedSpec.token = formData.rathole_token
        }
        if (formData.node_ipv6) {
          updatedSpec.node_ipv6 = formData.node_ipv6
        }
        if (formData.rathole_transport) {
          updatedSpec.transport = formData.rathole_transport
          updatedSpec.transport_type = formData.rathole_transport
        }
        if (formData.custom_sni) {
          updatedSpec.custom_sni = formData.custom_sni
          updatedSpec.stealth_domain = formData.custom_sni
        }
        updatedSpec.ports = ports
        updatedSpec.remote_port = ports[0]  // Keep for backward compatibility
        updatedSpec.listen_port = ports[0]  // Keep for backward compatibility
      } else if (tunnel.core === 'gost' && (tunnel.type === 'tcp' || tunnel.type === 'udp' || tunnel.type === 'tcp+udp' || tunnel.type === 'grpc' || tunnel.type === 'tcpmux')) {
        const remoteIp = formData.remote_ip || '127.0.0.1'
        updatedSpec.remote_ip = remoteIp
        updatedSpec.ports = ports
        updatedSpec.port_ranges = port_ranges
        const fallbackPort = ports.length > 0 ? ports[0] : (port_ranges.length > 0 ? parseInt(port_ranges[0].split('-')[0]) : 8080)
        updatedSpec.remote_port = fallbackPort  // Keep for backward compatibility
        updatedSpec.listen_port = fallbackPort  // Keep for backward compatibility
        updatedSpec.is_reverse = Boolean(formData.is_reverse)
        updatedSpec.force_direct = !formData.is_reverse
      } else if (tunnel.core === 'chisel') {
        updatedSpec.ports = ports
        updatedSpec.port_ranges = port_ranges
        updatedSpec.is_reverse = Boolean(formData.is_reverse)
        updatedSpec.force_direct = !formData.is_reverse
        const firstPort = ports[0]
        updatedSpec.listen_port = firstPort
        updatedSpec.remote_port = firstPort
        const controlPort = formData.chisel_control_port 
          ? parseInt(formData.chisel_control_port.toString())
          : firstPort + 10000
        updatedSpec.control_port = controlPort
        updatedSpec.transport = formData.chisel_transport || 'ws'
        updatedSpec.transport_type = formData.chisel_transport || 'ws'
        if (formData.chisel_backend_url) {
          updatedSpec.backend_url = formData.chisel_backend_url
        } else {
          delete updatedSpec.backend_url
        }
        if (formData.chisel_custom_sni) {
          updatedSpec.custom_sni = formData.chisel_custom_sni
          updatedSpec.stealth_domain = formData.chisel_custom_sni
        } else {
          delete updatedSpec.custom_sni
          delete updatedSpec.stealth_domain
        }
        if (formData.chisel_custom_host) {
          updatedSpec.custom_host = formData.chisel_custom_host
        } else {
          delete updatedSpec.custom_host
        }
        if (formData.chisel_keepalive) {
          updatedSpec.keepalive = formData.chisel_keepalive
        }
        if (formData.node_ipv6) {
          updatedSpec.node_ipv6 = formData.node_ipv6
        }
      } else if (tunnel.core === 'frp') {
        const bindPort = parseInt(formData.frp_bind_port) || 7000
        updatedSpec.bind_port = bindPort
        updatedSpec.ports = ports
        updatedSpec.listen_port = ports[0]  // Keep for backward compatibility
        updatedSpec.remote_port = ports[0]  // Keep for backward compatibility
        if (formData.frp_token) {
          updatedSpec.token = formData.frp_token
        } else {
          delete updatedSpec.token
        }
        updatedSpec.local_ip = formData.frp_local_ip || '127.0.0.1'
        updatedSpec.local_port = ports[0]  // Keep for backward compatibility
        updatedSpec.type = (tunnel.type === 'udp' || tunnel.type === 'tcp+udp' || tunnel.type === 'http' || tunnel.type === 'https') ? tunnel.type : 'tcp'
        updatedSpec.transport_type = formData.frp_transport || 'tcp'
        updatedSpec.transport = formData.frp_transport || 'tcp'
        updatedSpec.security_type = formData.frp_security || 'tls'
        updatedSpec.custom_sni = formData.frp_sni || ''
        updatedSpec.use_encryption = formData.frp_encryption
        updatedSpec.use_compression = formData.frp_compression
        updatedSpec.enable_health_check = formData.frp_health_check !== false
        updatedSpec.health_check_type = formData.frp_health_check !== false ? (tunnel.type === 'http' ? 'http' : 'tcp') : null
        if (formData.frp_bandwidth_limit) {
          updatedSpec.bandwidth_limit = formData.frp_bandwidth_limit
        } else {
          delete updatedSpec.bandwidth_limit
        }
        if (formData.frp_proxy_protocol && formData.frp_proxy_protocol !== 'none') {
          updatedSpec.proxy_protocol_version = formData.frp_proxy_protocol
        } else {
          delete updatedSpec.proxy_protocol_version
        }
        if (formData.frp_custom_domains) {
          updatedSpec.custom_domains = formData.frp_custom_domains.split(',').map((d: string) => d.trim()).filter(Boolean)
        } else {
          delete updatedSpec.custom_domains
        }
        updatedSpec.is_reverse = formData.is_reverse !== false
        updatedSpec.force_direct = formData.is_reverse === false
      } else if (tunnel.core === 'backhaul') {
        updatedSpec = buildBackhaulSpec(backhaulState, backhaulAdvanced, backhaulState.transport)
        if ((!updatedSpec.ports || updatedSpec.ports.length === 0) && ports.length > 0) {
          const targetHost = updatedSpec.target_host || '127.0.0.1'
          updatedSpec.ports = ports.map(p => `${p}=${targetHost}:${p}`)
        }
      }

      if (tunnel.core === 'gost' && formData.ws_path && !formData.ws_path.startsWith('/')) {
        showToast('warning', 'Validation', 'WS Path must start with a slash (e.g., /graphql)')
        return
      }

      await api.put(`/tunnels/${tunnel.id}`, {
        name: formData.name,
        category: formData.category ? formData.category.trim() : null,
        spec: updatedSpec,
        transport_type: tunnel.core === 'backhaul'
          ? (backhaulState.transport || 'tcpmux')
          : (tunnel.core === 'rathole' ? (formData.rathole_transport || 'tcp') : (tunnel.core === 'frp' ? (formData.frp_transport || 'tcp') : (tunnel.core === 'chisel' ? (formData.chisel_transport || 'ws') : formData.transport_type))),
        ...(tunnel.core === 'backhaul' && {
          type: (backhaulState.accept_udp ? (tunnel.type === 'udp' ? 'udp' : 'tcp+udp') : 'tcp'),
          security_type: (backhaulState.transport === 'wss' || backhaulState.transport === 'wssmux') ? 'tls' : 'none',
          custom_sni: backhaulAdvanced.client.edge_ip || formData.custom_sni || null,
          gaming_mode: Boolean(backhaulState.gaming_mode),
          is_reverse: true,
          port_ranges: port_ranges.length > 0 ? port_ranges : null,
        }),
        ...(tunnel.core === 'frp' && {
          security_type: formData.frp_security || 'tls',
          custom_sni: formData.frp_sni || null,
          is_reverse: Boolean(formData.is_reverse),
        }),
        ...(tunnel.core === 'chisel' && {
          security_type: formData.chisel_transport === 'wss' ? 'tls' : 'none',
          custom_sni: formData.chisel_custom_sni || null,
          custom_host: formData.chisel_custom_host || null,
          is_reverse: Boolean(formData.is_reverse),
          port_ranges: port_ranges.length > 0 ? port_ranges : null,
        }),
        ...(tunnel.core === 'gost' && {
          cdn_mode: formData.cdn_mode,
          gaming_mode: formData.gaming_mode,
          custom_host: formData.custom_host,
          custom_sni: formData.custom_sni,
          ws_path: formData.ws_path,
          is_reverse: Boolean(formData.is_reverse),
          stealth_domain: formData.stealth_domain || null,
          security_type: formData.security_type,
          selector_strategy: formData.selector_strategy || 'fifo',
          utls_fingerprint: formData.security_type === 'utls' ? (formData.utls_fingerprint || 'chrome') : null,
          keepalive_interval: formData.keepalive_interval ? parseInt(String(formData.keepalive_interval)) : 15,
          failover_ips: formData.failover_ips ? formData.failover_ips.split('\n').map((ip: string) => ip.trim()).filter((ip: string) => ip.length > 0) : null,
          rate_limit_mbps: formData.rate_limit_enabled && formData.rate_limit_mbps ? parseFloat(formData.rate_limit_mbps) : null,
          allowed_ips: formData.allowed_ips_enabled && formData.allowed_ips 
            ? formData.allowed_ips.split('\n').map((ip: string) => ip.trim()).filter((ip: string) => ip.length > 0)
            : null,
          port_ranges: port_ranges.length > 0 ? port_ranges : null
        }),
        node_id: (formData.is_reverse !== false) ? (formData.iran_node_id || tunnel.iran_node_id || tunnel.node_id) : (formData.foreign_node_id || tunnel.foreign_node_id || tunnel.node_id),
        iran_node_id: formData.iran_node_id || tunnel.iran_node_id || tunnel.node_id || undefined,
        foreign_node_id: formData.foreign_node_id || tunnel.foreign_node_id || undefined
      })
      showToast('success', 'Configuration Saved', `${formData.name} was saved safely. Active tunnel remains live until you click Reapply.`)
      onSuccess()
    } catch (error: any) {
      console.error('Failed to update tunnel:', error)
      const detail = error.response?.data?.detail
      let errorMsg = 'Failed to update tunnel'
      if (typeof detail === 'string') {
        errorMsg = detail
      } else if (Array.isArray(detail)) {
        errorMsg = detail.map((d: any) => d.msg || (typeof d === 'string' ? d : JSON.stringify(d))).join(', ')
      } else if (error.message) {
        errorMsg = error.message
      }
      showToast('error', 'Error', errorMsg)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-xs flex items-center justify-center z-[100] p-3.5 sm:p-4">
      <div className="bg-white dark:bg-gray-800 rounded-2xl w-full max-w-2xl max-h-[90dvh] shadow-2xl border border-gray-200/80 dark:border-gray-700/80 flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <form onSubmit={handleSubmit} className="flex flex-col h-full max-h-[90dvh] overflow-hidden">
          {/* Sticky Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 dark:border-gray-700/70 bg-gray-50/70 dark:bg-gray-800/90 shrink-0">
            <div className="flex items-center gap-2.5 min-w-0">
              <span className="p-1.5 rounded-lg bg-blue-100 dark:bg-blue-900/50 text-blue-600 dark:text-blue-400 shrink-0">
                <Edit2 size={18} />
              </span>
              <h2 className="text-lg sm:text-xl font-bold text-gray-900 dark:text-white truncate">
                Edit Tunnel: <span className="text-blue-600 dark:text-blue-400 font-mono text-base sm:text-lg">{tunnel.name}</span>
              </h2>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors cursor-pointer shrink-0 ml-2"
              title="Close"
            >
              <X size={18} />
            </button>
          </div>

          {/* Scrollable Body */}
          <div className="flex-1 overflow-y-auto custom-scrollbar p-4 sm:p-6 space-y-4 min-h-0">
            <div className="p-3 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 rounded-xl">
              <p className="text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5">
                <ShieldCheck size={14} className="shrink-0 text-emerald-600 dark:text-emerald-400" />
                <span>Zero-Downtime: Saving changes will not drop live connections until you click Reapply.</span>
              </p>
            </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                {t.tunnels.name}
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-base sm:text-sm font-medium focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
                required
              />
            </div>
            <div>
              <div className="flex items-center justify-between mb-1 h-5">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 flex items-center gap-1">
                  <Tag size={13} className="text-blue-500" />
                  <span>{t.tunnels.categories || 'Category'}</span>
                </label>
                <button
                  type="button"
                  onClick={() => setShowInlineNewCategory(prev => !prev)}
                  className="text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline cursor-pointer"
                >
                  {showInlineNewCategory ? 'Cancel' : '+ New'}
                </button>
              </div>
              <CustomSelect
                value={formData.category || ''}
                onChange={(val) => setFormData({ ...formData, category: val })}
                options={[
                  { value: '', label: t.tunnels.uncategorized || 'No Category', icon: <FolderMinus size={14} className="text-slate-400" /> },
                  ...categories.map((c) => ({
                    value: c.name,
                    label: c.name,
                    icon: <Tag size={13} className={getCategoryColorClasses(c.color).text} />
                  }))
                ]}
                placeholder={t.tunnels.uncategorized || 'No Category'}
              />
              {showInlineNewCategory && (
                <div className="mt-2 p-2 rounded-xl bg-gray-50 dark:bg-gray-700/60 border border-gray-200 dark:border-gray-600 flex gap-1.5">
                  <input
                    type="text"
                    placeholder="New category..."
                    value={inlineCategoryName}
                    onChange={(e) => setInlineCategoryName(e.target.value)}
                    className="flex-1 px-2.5 py-1 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <button
                    type="button"
                    onClick={async () => {
                      if (!inlineCategoryName.trim()) return
                      const created = await onCategoryCreated?.(inlineCategoryName.trim())
                      if (created) {
                        setFormData({ ...formData, category: created.name })
                        setInlineCategoryName('')
                        setShowInlineNewCategory(false)
                      }
                    }}
                    className="px-2.5 py-1 bg-blue-600 text-white rounded-lg text-xs font-semibold hover:bg-blue-700 cursor-pointer"
                  >
                    Save
                  </button>
                </div>
              )}
            </div>
          </div>
          {tunnel.core === 'gost' && (tunnel.type === 'tcp' || tunnel.type === 'udp' || tunnel.type === 'tcp+udp' || tunnel.type === 'grpc' || tunnel.type === 'tcpmux') && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
              <div>
                <div className="flex items-center justify-between mb-1 h-5">
                  <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                    {t.tunnels.remoteIP}
                  </label>
                </div>
                <input
                  type="text"
                  value={formData.remote_ip}
                  onChange={(e) =>
                    setFormData({ ...formData, remote_ip: e.target.value || '127.0.0.1' })
                  }
                  className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-base sm:text-sm font-mono focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
                  placeholder="127.0.0.1 or [2001:db8::1]"
                />
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  {t.tunnels.remoteIPDescription}
                </p>
              </div>
              <div>
                <div className="flex items-center justify-between mb-1 h-5">
                  <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                    Ports & Ranges
                  </label>
                </div>
                <input
                  type="text"
                  value={formData.ports}
                  onChange={(e) =>
                    setFormData({ ...formData, ports: e.target.value })
                  }
                  className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-base sm:text-sm font-mono focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
                  placeholder="8080, 8081, 10000-20000"
                />
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Ports or ranges (comma-separated, same for panel and target server)
                </p>
              </div>
            </div>
          )}
          
          {tunnel.core === 'backhaul' && (
            <BackhaulForm
              state={backhaulState}
              onChange={(partial: Partial<BackhaulFormState>) => {
                setBackhaulState((prev) => ({ ...prev, ...partial }))
                if (partial.transport) {
                  setFormData((prev: any) => ({ ...prev, transport_type: partial.transport as string }))
                }
                if (partial.accept_udp !== undefined) {
                  if (partial.accept_udp && formData.type === 'tcp') {
                    setFormData((prev: any) => ({ ...prev, type: 'tcp+udp' }))
                  } else if (!partial.accept_udp && formData.type !== 'tcp') {
                    setFormData((prev: any) => ({ ...prev, type: 'tcp' }))
                  }
                }
                if (partial.gaming_mode !== undefined) {
                  setFormData((prev) => ({ ...prev, gaming_mode: partial.gaming_mode }))
                  if (partial.gaming_mode) {
                    setBackhaulAdvanced((prev) => ({
                      ...prev,
                      server: {
                        ...prev.server,
                        nodelay: true,
                        channel_size: '8192',
                        mux_framesize: '4096',
                        mss: '1380',
                        keepalive_period: '12',
                        heartbeat: '12',
                        so_rcvbuf: '2097152',
                        so_sndbuf: '2097152',
                      },
                      client: {
                        ...prev.client,
                        nodelay: true,
                        channel_size: '8192',
                        mux_framesize: '4096',
                        mss: '1380',
                        keepalive_period: '12',
                        heartbeat: '12',
                        so_rcvbuf: '2097152',
                        so_sndbuf: '2097152',
                        aggressive_pool: true,
                      }
                    }))
                  }
                }
              }}
              onOpenAdvanced={() => setShowBackhaulAdvanced(true)}
              acceptUdpVisible={
                backhaulState.transport === 'tcp' || backhaulState.transport === 'tcpmux' || backhaulState.transport === 'udp'
              }
            />
          )}
          
          {/* Rathole Core Settings */}
          {tunnel.core === 'rathole' && (
            <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-orange-50/60 via-gray-50 to-amber-50/40 dark:from-orange-950/20 dark:via-gray-800/60 dark:to-amber-950/20 border border-orange-200/70 dark:border-orange-900/40 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
                <div className="flex items-center gap-2">
                  <span className="p-1.5 rounded-lg bg-orange-100 dark:bg-orange-900/60 text-orange-600 dark:text-orange-300">
                    <Cpu size={16} />
                  </span>
                  <div>
                    <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">
                      Rathole Settings
                    </h4>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      Ultra-Lightweight, Secure NAT-Traversal Core in Rust
                    </p>
                  </div>
                </div>
                <span className="self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium bg-orange-100/80 text-orange-700 dark:bg-orange-900/60 dark:text-orange-300 border border-orange-200 dark:border-orange-800">
                  Rathole Core
                </span>
              </div>

              {/* Quick Presets for Rathole */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                    <Sparkles size={14} className="text-orange-600 dark:text-orange-400" />
                    Quick Presets (1-Click Optimization)
                  </span>
                  <span className="text-[11px] text-gray-400 dark:text-gray-500">Auto-configures transport & encryption</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        rathole_transport: 'noise',
                        custom_sni: ''
                      }));
                      showToast('info', 'Preset Applied', 'Noise Protocol (Gaming & Anti-DPI) applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.rathole_transport === 'noise'
                        ? 'bg-orange-100/80 dark:bg-orange-950/50 border-orange-400 dark:border-orange-500 ring-2 ring-orange-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-orange-50 dark:hover:bg-orange-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Shield size={16} className="text-orange-600 dark:text-orange-400" />
                      <span className="text-xs font-bold text-orange-700 dark:text-orange-300">Noise Protocol</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      WireGuard-grade encrypted tunnel. Zero handshake lag.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        rathole_transport: 'wss',
                        custom_sni: 'dl.google.com'
                      }));
                      showToast('info', 'Preset Applied', 'WSS Camouflage (TLS + CDN Capable) applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.rathole_transport === 'wss'
                        ? 'bg-blue-100/80 dark:bg-blue-950/50 border-blue-400 dark:border-blue-500 ring-2 ring-blue-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-blue-50 dark:hover:bg-blue-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Globe size={16} className="text-blue-600 dark:text-blue-400" />
                      <span className="text-xs font-bold text-blue-700 dark:text-blue-300">WSS Stealth</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      HTTPS WebSocket + TLS SNI spoofing for anti-DPI.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        rathole_transport: 'tcp',
                        custom_sni: ''
                      }));
                      showToast('info', 'Preset Applied', 'Standard TCP applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      (!formData.rathole_transport || formData.rathole_transport === 'tcp')
                        ? 'bg-gray-100/80 dark:bg-gray-700/50 border-gray-400 dark:border-gray-500 ring-2 ring-gray-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-gray-50 dark:hover:bg-gray-700/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Zap size={16} className="text-gray-600 dark:text-gray-400" />
                      <span className="text-xs font-bold text-gray-700 dark:text-gray-300">TCP Standard</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      Raw direct TCP stream. Lowest CPU overhead.
                    </p>
                  </button>
                </div>
              </div>

              {/* Ports & Connectivity Grid */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-start pt-1">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Radio size={14} className="text-orange-500" />
                      Forwarded Ports
                    </label>
                  </div>
                  <input
                    type="text"
                    value={formData.ports}
                    onChange={(e) => setFormData({ ...formData, ports: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 transition-all font-mono"
                    placeholder="8080,8081,8082"
                    required
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Ports mapped between panel and node.
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Server size={14} className="text-amber-500" />
                      Rathole Port
                    </label>
                    <button
                      type="button"
                      onClick={() => {
                        const port = generateRandomControlPort()
                        const host = window.location.hostname
                        setFormData({ ...formData, rathole_remote_addr: `${host}:${port}` })
                      }}
                      className="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-medium bg-orange-50 dark:bg-orange-950/40 text-orange-600 dark:text-orange-400 hover:bg-orange-100 dark:hover:bg-orange-900/60 border border-orange-200/60 dark:border-orange-800/40 transition-all cursor-pointer shadow-xs"
                      title="Generate Random Port"
                    >
                      <Dices size={12} />
                      <span>Random</span>
                    </button>
                  </div>
                  <input
                    type="number"
                    value={formData.rathole_remote_addr ? formData.rathole_remote_addr.split(':')[1] || formData.rathole_remote_addr : ''}
                    onChange={(e) => {
                      const port = e.target.value
                      const host = window.location.hostname
                      setFormData({ ...formData, rathole_remote_addr: port ? `${host}:${port}` : '' })
                    }}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 transition-all font-mono"
                    placeholder="23333"
                    min="1"
                    max="65535"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Panel server bind port.
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Server size={14} className="text-emerald-500" />
                      Local Port
                    </label>
                  </div>
                  <input
                    type="number"
                    value={formData.rathole_local_port}
                    onChange={(e) => setFormData({ ...formData, rathole_local_port: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 transition-all font-mono"
                    placeholder="8080"
                    min="1"
                    max="65535"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Local service destination port.
                  </p>
                </div>
              </div>

              {/* Transport Protocol Select & Token */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-orange-500"></span>
                      Transport Protocol
                    </label>
                    <span className="text-[11px] font-mono uppercase text-gray-400">
                      {formData.rathole_transport || 'tcp'}
                    </span>
                  </div>
                  <CustomSelect
                    value={formData.rathole_transport || 'tcp'}
                    onChange={(val) => setFormData({ ...formData, rathole_transport: val })}
                    options={[
                      { value: 'tcp', label: 'TCP (Standard Raw)' },
                      { value: 'noise', label: 'Noise Protocol (Encrypted / Gaming / Anti-DPI)' },
                      { value: 'ws', label: 'WebSocket (WS)' },
                      { value: 'wss', label: 'WebSocket + TLS (WSS)' },
                    ]}
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    {formData.rathole_transport === 'noise' && 'WireGuard-grade 256-bit encryption with zero-handshake delay.'}
                    {formData.rathole_transport === 'wss' && 'Standard TLS 1.3 encapsulation for anti-DPI camouflage.'}
                    {formData.rathole_transport === 'ws' && 'Standard WebSocket stream.'}
                    {(!formData.rathole_transport || formData.rathole_transport === 'tcp') && 'Raw TCP stream without encryption wrapper.'}
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Key size={14} className="text-indigo-500" />
                      Auth Token
                    </label>
                    <span className="text-[11px] text-gray-400">Optional</span>
                  </div>
                  <input
                    type="text"
                    value={formData.rathole_token}
                    onChange={(e) => setFormData({ ...formData, rathole_token: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all font-mono"
                    placeholder="Auto-generated if empty"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Cryptographic secret token shared between panel and client.
                  </p>
                </div>
              </div>

              {/* SNI Camouflage */}
              {(formData.rathole_transport === 'wss' || formData.rathole_transport === 'ws') && (
                <div className="p-3.5 rounded-xl bg-orange-50/70 dark:bg-orange-950/20 border border-orange-200/80 dark:border-orange-900/50 space-y-2">
                  <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                    <EyeOff size={14} className="text-orange-600 dark:text-orange-400" />
                    Custom SNI / Domain Camouflage
                  </label>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400">
                    TLS Server Name Indication (SNI) spoofing to mimic legitimate web services:
                  </p>
                  <input
                    type="text"
                    value={formData.custom_sni || ''}
                    onChange={(e) => setFormData({ ...formData, custom_sni: e.target.value })}
                    placeholder="e.g. dl.google.com or cdn.cloudflare.com"
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-lg border border-orange-300 dark:border-orange-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500/20 font-medium"
                  />
                </div>
              )}

              {/* Node IPv6 address field for Rathole when v4 to v6 is enabled */}
              {tunnel.spec?.use_ipv6 && (
                <div className="p-3.5 rounded-xl bg-gray-50/70 dark:bg-gray-800/40 border border-gray-200 dark:border-gray-700 space-y-1.5">
                  <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                    <Globe size={14} className="text-blue-500" />
                    Node IPv6 Address (Optional)
                  </label>
                  <input
                    type="text"
                    value={formData.node_ipv6}
                    onChange={(e) => setFormData({ ...formData, node_ipv6: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500/20 font-mono"
                    placeholder="::1 or 2001:db8::1"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400">
                    IPv6 address of the target node (defaults to ::1 localhost IPv6).
                  </p>
                </div>
              )}
            </div>
          )}

          {/* Chisel Core Settings */}
          {tunnel.core === 'chisel' && (
            <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-teal-50/60 via-gray-50 to-emerald-50/40 dark:from-teal-950/20 dark:via-gray-800/60 dark:to-emerald-950/20 border border-teal-200/70 dark:border-teal-900/40 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
                <div className="flex items-center gap-2">
                  <span className="p-1.5 rounded-lg bg-teal-100 dark:bg-teal-900/60 text-teal-600 dark:text-teal-300">
                    <Terminal size={16} />
                  </span>
                  <div>
                    <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">
                      Chisel Advanced Settings
                    </h4>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      High-Speed WebSocket Tunnel with SSH Multiplexing, WSS TLS & Anti-DPI Camouflage
                    </p>
                  </div>
                </div>
                <span className="self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium bg-teal-100/80 text-teal-700 dark:bg-teal-900/60 dark:text-teal-300 border border-teal-200 dark:border-teal-800">
                  Chisel Core
                </span>
              </div>

              {/* Quick Preset Buttons */}
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                  <Sparkles size={14} className="text-teal-500" />
                  Quick Optimization Presets
                </label>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        chisel_transport: 'wss',
                        chisel_backend_url: 'https://speedtest.net',
                        chisel_custom_sni: 'speedtest.net',
                        chisel_keepalive: '10s'
                      }));
                      showToast('info', 'Preset Applied', 'Anti-DPI Stealth Preset (WSS + Camouflage + SNI) configured');
                    }}
                    className={`p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.chisel_transport === 'wss' && formData.chisel_backend_url
                        ? 'bg-teal-100/80 dark:bg-teal-900/50 border-teal-400 dark:border-teal-500 ring-2 ring-teal-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-teal-50 dark:hover:bg-teal-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Shield size={15} className="text-teal-600 dark:text-teal-400" />
                      <span className="text-xs font-bold text-teal-800 dark:text-teal-200">Anti-DPI Stealth</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      WSS TLS + speedtest.net decoy camouflage against active probers.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        chisel_keepalive: '5s'
                      }));
                      showToast('info', 'Preset Applied', 'Ultra-Low Ping Gaming (5s Keepalive) configured');
                    }}
                    className={`p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.chisel_keepalive === '5s'
                        ? 'bg-amber-100/80 dark:bg-amber-900/50 border-amber-400 dark:border-amber-500 ring-2 ring-amber-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-amber-50 dark:hover:bg-amber-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Zap size={15} className="text-amber-500 dark:text-amber-400" />
                      <span className="text-xs font-bold text-amber-700 dark:text-amber-300">Ultra-Low Ping</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      Aggressive 5s Keepalive to prevent NAT firewall table drops.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        chisel_transport: 'ws',
                        chisel_custom_host: 'cdn.cloudflare.com',
                        chisel_keepalive: '15s'
                      }));
                      showToast('info', 'Preset Applied', 'CDN-Friendly WebSocket Preset configured');
                    }}
                    className={`p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.chisel_transport === 'ws' && formData.chisel_custom_host === 'cdn.cloudflare.com'
                        ? 'bg-blue-100/80 dark:bg-blue-900/50 border-blue-400 dark:border-blue-500 ring-2 ring-blue-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-blue-50 dark:hover:bg-blue-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Globe size={15} className="text-blue-500 dark:text-blue-400" />
                      <span className="text-xs font-bold text-blue-700 dark:text-blue-300">CDN Friendly</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      HTTP/WS with Host spoofing compatible with reverse proxies.
                    </p>
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Radio size={14} className="text-teal-500" />
                      Forwarded Ports
                    </label>
                    <span className="text-[11px] text-gray-400">Reverse & Local</span>
                  </div>
                  <input
                    type="text"
                    value={formData.ports}
                    onChange={(e) => setFormData({ ...formData, ports: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500 transition-all font-mono"
                    placeholder="8080,8081,8082"
                    required
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Comma-separated ports mapped through the tunnel.
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Server size={14} className="text-emerald-500" />
                      Control Port
                    </label>
                    <button
                      type="button"
                      onClick={() => setFormData({ ...formData, chisel_control_port: generateRandomControlPort() })}
                      className="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-medium bg-teal-50 dark:bg-teal-950/40 text-teal-600 dark:text-teal-400 hover:bg-teal-100 dark:hover:bg-teal-900/60 border border-teal-200/60 dark:border-teal-800/40 transition-all cursor-pointer shadow-xs"
                      title="Generate Random Control Port"
                    >
                      <Dices size={12} />
                      <span>Random</span>
                    </button>
                  </div>
                  <input
                    type="number"
                    value={formData.chisel_control_port}
                    onChange={(e) => setFormData({ ...formData, chisel_control_port: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-teal-500/20 focus:border-teal-500 transition-all font-mono"
                    placeholder={`${(parseInt(formData.ports.split(',')[0]?.trim()) || 8080) + 10000} (auto)`}
                    min="1"
                    max="65535"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Server control port (defaults to first port + 10000 if empty).
                  </p>
                </div>
              </div>

              {/* Transport Protocol & Keepalive */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Shield size={14} className="text-teal-500" />
                      Transport Protocol & Encryption
                    </label>
                    <span className="text-[11px] font-mono uppercase text-gray-400 dark:text-gray-500">
                      {formData.chisel_transport}
                    </span>
                  </div>
                  <CustomSelect
                    value={formData.chisel_transport}
                    onChange={(val) => setFormData({ ...formData, chisel_transport: val })}
                    options={[
                      { value: 'ws', label: 'WS - Plain WebSocket (HTTP, Low Overhead)' },
                      { value: 'wss', label: 'WSS - Encrypted WebSocket over TLS (HTTPS)' },
                    ]}
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    {formData.chisel_transport === 'wss'
                      ? 'Outer TLS encryption layer + Inner SSH stream encryption (Double Layer Security).'
                      : 'Standard WebSocket with built-in inner SSH encryption layer.'}
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Zap size={14} className="text-amber-500" />
                      Keepalive Heartbeat
                    </label>
                    <span className="text-[11px] font-mono text-gray-400 dark:text-gray-500">
                      {formData.chisel_keepalive}
                    </span>
                  </div>
                  <CustomSelect
                    value={formData.chisel_keepalive}
                    onChange={(val) => setFormData({ ...formData, chisel_keepalive: val })}
                    options={[
                      { value: '5s', label: '5s (Ultra-Aggressive / Competitive Gaming)' },
                      { value: '10s', label: '10s (Recommended - High Stability)' },
                      { value: '15s', label: '15s (Balanced)' },
                      { value: '25s', label: '25s (Default / Low Overhead)' },
                    ]}
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Prevents firewall NAT translation state dropouts across Iranian ISPs.
                  </p>
                </div>
              </div>

              {/* Anti-Probing Camouflage & Decoy Website */}
              <div className="p-3.5 rounded-xl bg-teal-50/70 dark:bg-teal-950/20 border border-teal-200/70 dark:border-teal-900/40 space-y-3">
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs font-bold text-teal-900 dark:text-teal-200 flex items-center gap-1.5">
                      <Shield size={14} className="text-teal-600 dark:text-teal-400" />
                      Decoy Camouflage Backend (--backend)
                    </label>
                    <span className="text-[10px] px-2 py-0.5 rounded-md font-medium bg-teal-200/60 text-teal-800 dark:bg-teal-900/60 dark:text-teal-300">
                      Anti-Active Probing
                    </span>
                  </div>
                  <p className="text-[11px] text-teal-800/80 dark:text-teal-300/80 mb-2">
                    When censors or scanning bots probe your control port via standard HTTP/HTTPS, Chisel proxies the request to this legitimate website with HTTP 200 OK:
                  </p>
                  <input
                    type="text"
                    value={formData.chisel_backend_url}
                    onChange={(e) => setFormData({ ...formData, chisel_backend_url: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-teal-300 dark:border-teal-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white font-mono"
                    placeholder="https://speedtest.net or https://example.com"
                  />
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
                  <div>
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 block mb-1">
                      Custom SNI (--sni)
                    </label>
                    <input
                      type="text"
                      value={formData.chisel_custom_sni}
                      onChange={(e) => setFormData({ ...formData, chisel_custom_sni: e.target.value })}
                      className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white font-mono"
                      placeholder="speedtest.net or domain.com"
                    />
                    <p className="text-[10px] text-gray-500 dark:text-gray-400 mt-1">
                      Overrides TLS ClientHello Server Name Indication.
                    </p>
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 block mb-1">
                      Custom Host Header (--hostname)
                    </label>
                    <input
                      type="text"
                      value={formData.chisel_custom_host}
                      onChange={(e) => setFormData({ ...formData, chisel_custom_host: e.target.value })}
                      className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white font-mono"
                      placeholder="cdn.speedtest.net"
                    />
                    <p className="text-[10px] text-gray-500 dark:text-gray-400 mt-1">
                      Sets HTTP Host header for reverse proxies / CDN fronting.
                    </p>
                  </div>
                </div>
              </div>

              {tunnel.spec?.use_ipv6 && (
                <div className="p-3.5 rounded-xl bg-gray-50/70 dark:bg-gray-800/40 border border-gray-200 dark:border-gray-700 space-y-1.5">
                  <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                    <Globe size={14} className="text-teal-500" />
                    Node IPv6 Address (Optional)
                  </label>
                  <input
                    type="text"
                    value={formData.node_ipv6}
                    onChange={(e) => setFormData({ ...formData, node_ipv6: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-teal-500/20 font-mono"
                    placeholder="::1 or 2001:db8::1"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400">
                    IPv6 address of the node (defaults to ::1 localhost IPv6).
                  </p>
                </div>
              )}
            </div>
          )}

          {/* FRP Core Settings */}
          {tunnel.core === 'frp' && (
            <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-cyan-50/60 via-gray-50 to-blue-50/40 dark:from-cyan-950/20 dark:via-gray-800/60 dark:to-blue-950/20 border border-cyan-200/70 dark:border-cyan-900/40 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
                <div className="flex items-center gap-2">
                  <span className="p-1.5 rounded-lg bg-cyan-100 dark:bg-cyan-900/60 text-cyan-600 dark:text-cyan-300">
                    <Layers size={16} />
                  </span>
                  <div>
                    <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">
                      FRP Advanced Settings
                    </h4>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      High-Performance Reverse Proxy with Multiplexing & Encryption
                    </p>
                  </div>
                </div>
                <span className="self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium bg-cyan-100/80 text-cyan-700 dark:bg-cyan-900/60 dark:text-cyan-300 border border-cyan-200 dark:border-cyan-800">
                  FRP v0.50+ Core
                </span>
              </div>

              {/* Quick Presets for FRP */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                    <Sparkles size={14} className="text-cyan-600 dark:text-cyan-400" />
                    Quick Presets (1-Click Optimization)
                  </span>
                  <span className="text-[11px] text-gray-400 dark:text-gray-500">Auto-configures transport & encryption</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        frp_transport: 'tcp',
                        frp_encryption: true,
                        frp_compression: false,
                      }));
                      showToast('info', 'Preset Applied', 'TCP + Native TLS Fast Gaming applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.frp_transport === 'tcp'
                        ? 'bg-cyan-100/80 dark:bg-cyan-950/50 border-cyan-400 dark:border-cyan-500 ring-2 ring-cyan-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-cyan-50 dark:hover:bg-cyan-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Zap size={16} className="text-cyan-600 dark:text-cyan-400" />
                      <span className="text-xs font-bold text-cyan-700 dark:text-cyan-300">Fast TCP + TLS</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      FRP Native TLS over TCP. Fast, low jitter, bypasses DPI (Best for Gaming/UDP).
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        frp_transport: 'wss',
                        frp_sni: 'dl.google.com',
                        frp_encryption: true,
                        frp_compression: true,
                      }));
                      showToast('info', 'Preset Applied', 'WSS Stealth / Anti-DPI applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.frp_transport === 'wss'
                        ? 'bg-indigo-100/80 dark:bg-indigo-950/50 border-indigo-400 dark:border-indigo-500 ring-2 ring-indigo-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-indigo-50 dark:hover:bg-indigo-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Globe size={16} className="text-indigo-600 dark:text-indigo-400" />
                      <span className="text-xs font-bold text-indigo-700 dark:text-indigo-300">WSS Stealth</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      Secure WebSocket + SNI camouflage. CDN friendly.
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormData(prev => ({
                        ...prev,
                        frp_transport: 'kcp',
                        frp_encryption: true,
                        frp_compression: false,
                      }));
                      showToast('info', 'Preset Applied', 'KCP Anti-Packet-Loss applied');
                    }}
                    className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                      formData.frp_transport === 'kcp'
                        ? 'bg-emerald-100/80 dark:bg-emerald-950/50 border-emerald-400 dark:border-emerald-500 ring-2 ring-emerald-400/20'
                        : 'bg-white/80 dark:bg-gray-800/80 hover:bg-emerald-50 dark:hover:bg-emerald-950/30 border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Rocket size={16} className="text-emerald-600 dark:text-emerald-400" />
                      <span className="text-xs font-bold text-emerald-700 dark:text-emerald-300">KCP Anti-Loss</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                      Aggressive ARQ retransmission for unstable routes.
                    </p>
                  </button>
                </div>
              </div>

              {/* Bind Port & Ports */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Server size={14} className="text-cyan-500" />
                      FRP Bind Port
                    </label>
                    <button
                      type="button"
                      onClick={() => setFormData({ ...formData, frp_bind_port: generateRandomControlPort() })}
                      className="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-medium bg-cyan-50 dark:bg-cyan-950/40 text-cyan-600 dark:text-cyan-400 hover:bg-cyan-100 dark:hover:bg-cyan-900/60 border border-cyan-200/60 dark:border-cyan-800/40 transition-all cursor-pointer shadow-xs"
                      title="Generate Random Bind Port"
                    >
                      <Dices size={12} />
                      <span>Random</span>
                    </button>
                  </div>
                  <input
                    type="number"
                    value={formData.frp_bind_port}
                    onChange={(e) => setFormData({ ...formData, frp_bind_port: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-cyan-500/20 focus:border-cyan-500 transition-all font-mono"
                    placeholder="7000"
                    min="1"
                    max="65535"
                    required
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Server bind port on the panel machine.
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Radio size={14} className="text-blue-500" />
                      Forwarded Ports
                    </label>
                    <span className="text-[11px] text-gray-400">Remote & Local</span>
                  </div>
                  <input
                    type="text"
                    value={formData.ports}
                    onChange={(e) => setFormData({ ...formData, ports: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-cyan-500/20 focus:border-cyan-500 transition-all font-mono"
                    placeholder="8080,8081,8082"
                    required
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Comma-separated ports forwarded to remote service.
                  </p>
                </div>
              </div>

              {/* Transport & SNI */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-cyan-500"></span>
                      Transport Protocol
                    </label>
                    <span className="text-[11px] font-mono uppercase text-gray-400">
                      {formData.frp_transport || 'tcp'}
                    </span>
                  </div>
                  <CustomSelect
                    value={formData.frp_transport || 'tcp'}
                    onChange={(val) => setFormData({ ...formData, frp_transport: val })}
                    options={[
                      { value: 'tcp', label: 'TCP (with TLS & Zero-Byte Signature)' },
                      { value: 'kcp', label: 'KCP (Fast UDP - Resilient to Packet Loss)' },
                      { value: 'quic', label: 'QUIC (HTTP/3 UDP + TLS 1.3 Multiplex)' },
                      { value: 'websocket', label: 'WebSocket (Plain WS)' },
                      { value: 'wss', label: 'WSS (Secure WebSocket - CDN Capable)' },
                    ]}
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    {formData.frp_transport === 'quic' && 'Ultra-fast 0-RTT UDP multiplexing with TLS 1.3 encryption.'}
                    {formData.frp_transport === 'kcp' && 'Aggressive ARQ UDP for bad or filtered routes.'}
                    {formData.frp_transport === 'wss' && 'Encrypted WebSocket compatible with CDN reverse proxies.'}
                    {formData.frp_transport === 'websocket' && 'Standard HTTP WebSocket proxy.'}
                    {(!formData.frp_transport || formData.frp_transport === 'tcp') && 'Raw TCP stream with TLS signature hiding.'}
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <EyeOff size={14} className="text-purple-500" />
                      Stealth SNI / Camouflage Domain
                    </label>
                    <span className="text-[11px] text-gray-400">TLS Header</span>
                  </div>
                  <input
                    type="text"
                    value={formData.frp_sni}
                    onChange={(e) => setFormData({ ...formData, frp_sni: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-purple-500/20 focus:border-purple-500 transition-all font-medium"
                    placeholder="e.g. speedtest.net or dl.google.com"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Domain name inserted into TLS handshake to evade deep packet inspection.
                  </p>
                </div>
              </div>

              {/* Security Toggles Card */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                  <div className="pr-2">
                    <div className="flex items-center gap-1.5">
                      <Lock size={15} className="text-cyan-600 dark:text-cyan-400" />
                      <span className="text-xs font-bold text-gray-900 dark:text-white">Payload Encryption</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                      AES-128-CFB / ChaCha20 cipher
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    className="sr-only peer"
                    checked={formData.frp_encryption}
                    onChange={(e) => setFormData({ ...formData, frp_encryption: e.target.checked })}
                  />
                  <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-cyan-600 shrink-0 relative"></div>
                </label>

                <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                  <div className="pr-2">
                    <div className="flex items-center gap-1.5">
                      <Zap size={15} className="text-cyan-600 dark:text-cyan-400" />
                      <span className="text-xs font-bold text-gray-900 dark:text-white">Snappy Compression</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                      Reduces bandwidth & obfuscates entropy
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    className="sr-only peer"
                    checked={formData.frp_compression}
                    onChange={(e) => setFormData({ ...formData, frp_compression: e.target.checked })}
                  />
                  <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-cyan-600 shrink-0 relative"></div>
                </label>
              </div>

              {/* Auth Token */}
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
                  value={formData.frp_token}
                  onChange={(e) => setFormData({ ...formData, frp_token: e.target.value })}
                  className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all font-mono"
                  placeholder="Auto-generated if empty"
                />
                <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                  Shared secret token verifying client-server authorization.
                </p>
              </div>

              {/* Mode & Reliability */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                  <div className="pr-2">
                    <div className="flex items-center gap-1.5">
                      <ArrowLeftRight size={15} className="text-blue-600 dark:text-blue-400" />
                      <span className="text-xs font-bold text-gray-900 dark:text-white">Reverse Mode</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                      {formData.is_reverse ? 'Foreign connects to Iran (Recommended)' : 'Iran connects to Foreign (Direct STCP)'}
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    className="sr-only peer"
                    checked={formData.is_reverse}
                    onChange={(e) => setFormData({ ...formData, is_reverse: e.target.checked })}
                  />
                  <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                </label>

                <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                  <div className="pr-2">
                    <div className="flex items-center gap-1.5">
                      <Activity size={15} className="text-emerald-600 dark:text-emerald-400" />
                      <span className="text-xs font-bold text-gray-900 dark:text-white">Native Health Check</span>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                      Auto-detect dead links & reconnect fast
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    className="sr-only peer"
                    checked={formData.frp_health_check}
                    onChange={(e) => setFormData({ ...formData, frp_health_check: e.target.checked })}
                  />
                  <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-emerald-600 shrink-0 relative"></div>
                </label>
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                    <Network size={14} className="text-blue-500" />
                    Proxy Protocol Version
                  </label>
                </div>
                <CustomSelect
                  value={formData.frp_proxy_protocol || 'none'}
                  onChange={(val) => setFormData({ ...formData, frp_proxy_protocol: val })}
                  options={[
                    { value: 'none', label: 'Disabled (Direct)' },
                    { value: 'v1', label: 'v1 (ASCII Text)' },
                    { value: 'v2', label: 'v2 (Binary Fast)' },
                  ]}
                />
              </div>

              {/* Bandwidth Limit & Custom Domains */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Gauge size={14} className="text-amber-500" />
                      Bandwidth Rate Limit
                    </label>
                    <span className="text-[11px] text-gray-400">Optional</span>
                  </div>
                  <input
                    type="text"
                    value={formData.frp_bandwidth_limit}
                    onChange={(e) => setFormData({ ...formData, frp_bandwidth_limit: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-amber-500/20 focus:border-amber-500 transition-all font-mono"
                    placeholder="e.g. 10MB or 500KB"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Per-proxy bandwidth limit cap (e.g. 20MB).
                  </p>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Globe size={14} className="text-cyan-500" />
                      VHost Domains (Optional)
                    </label>
                    <span className="text-[11px] text-gray-400">HTTP/HTTPS</span>
                  </div>
                  <input
                    type="text"
                    value={formData.frp_custom_domains}
                    onChange={(e) => setFormData({ ...formData, frp_custom_domains: e.target.value })}
                    className="w-full px-3 py-2 text-sm sm:text-xs rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-cyan-500/20 focus:border-cyan-500 transition-all font-mono"
                    placeholder="e.g. app.domain.com"
                  />
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                    Custom domain routing for HTTP/HTTPS tunnels.
                  </p>
                </div>
              </div>
            </div>
          )}
          
          {/* Advanced GOST Settings */}
          {tunnel.core === 'gost' && (
            <div className="mt-6 border-t border-gray-200 dark:border-gray-700 pt-6">
              <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-br from-purple-50/60 via-gray-50 to-blue-50/40 dark:from-purple-950/20 dark:via-gray-800/60 dark:to-blue-950/20 border border-purple-200/70 dark:border-purple-900/40 space-y-4">
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-3 border-b border-gray-200/70 dark:border-gray-700/70">
                  <div className="flex items-center gap-2">
                    <span className="p-1.5 rounded-lg bg-purple-100 dark:bg-purple-900/60 text-purple-600 dark:text-purple-300">
                      <Zap size={16} />
                    </span>
                    <div>
                      <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">
                        Advanced GOST Settings
                      </h4>
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        Next-Gen Anti-DPI & High-Performance Routing Core
                      </p>
                    </div>
                  </div>
                  <span className="self-start sm:self-auto text-xs px-2.5 py-0.5 rounded-full font-mono font-medium bg-purple-100/80 text-purple-700 dark:bg-purple-900/60 dark:text-purple-300 border border-purple-200 dark:border-purple-800">
                    GOST v3.0 Core
                  </span>
                </div>

                {/* Quick Presets Grid */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <Sparkles size={14} className="text-purple-600 dark:text-purple-400" />
                      Quick Presets (1-Click Optimization)
                    </span>
                    <span className="text-[11px] text-gray-400 dark:text-gray-500">Auto-configures transport & security</span>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        setFormData(prev => ({
                          ...prev,
                          transport_type: 'grpc',
                          security_type: 'utls',
                          utls_fingerprint: 'chrome',
                          stealth_domain: 'www.google.com',
                          keepalive_interval: 15
                        }));
                        showToast('info', 'Preset Applied', 'Stealth Anti-DPI (gRPC + uTLS Chrome) applied');
                      }}
                      className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                        formData.transport_type === 'grpc' && formData.security_type === 'utls' && !formData.gaming_mode
                          ? 'bg-purple-100/80 dark:bg-purple-900/50 border-purple-400 dark:border-purple-500 ring-2 ring-purple-400/20'
                          : 'bg-white/80 dark:bg-gray-800/80 hover:bg-purple-50 dark:hover:bg-purple-950/30 border-gray-200 dark:border-gray-700'
                      }`}
                    >
                      <div className="flex items-center gap-1.5 mb-1">
                        <Shield size={16} className="text-purple-600 dark:text-purple-400" />
                        <span className="text-xs font-bold text-purple-700 dark:text-purple-300">Stealth Anti-DPI</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                        gRPC + uTLS Chrome. Bypasses deep packet inspection (Recommended for Iran).
                      </p>
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setFormData(prev => ({
                          ...prev,
                          transport_type: 'grpc',
                          security_type: 'utls',
                          utls_fingerprint: 'chrome',
                          stealth_domain: 'www.google.com',
                          gaming_mode: true,
                          keepalive_interval: 15
                        }));
                        showToast('info', 'Preset Applied', 'Ultra-Low Ping (gRPC + uTLS Chrome) applied');
                      }}
                      className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                        formData.transport_type === 'grpc' && formData.gaming_mode
                          ? 'bg-amber-100/80 dark:bg-amber-900/50 border-amber-400 dark:border-amber-500 ring-2 ring-amber-400/20'
                          : 'bg-white/80 dark:bg-gray-800/80 hover:bg-amber-50 dark:hover:bg-amber-950/30 border-gray-200 dark:border-gray-700'
                      }`}
                    >
                      <div className="flex items-center gap-1.5 mb-1">
                        <Zap size={16} className="text-amber-500 dark:text-amber-400" />
                        <span className="text-xs font-bold text-amber-700 dark:text-amber-300">Ultra-Low Ping</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                        gRPC + uTLS Chrome. 0-RTT Multiplexing & Lowest Jitter (Best for Gaming / UDP).
                      </p>
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setFormData(prev => ({
                          ...prev,
                          transport_type: 'wss',
                          security_type: 'tls',
                          stealth_domain: 'dl.google.com',
                          gaming_mode: true,
                          keepalive_interval: 15
                        }));
                        showToast('info', 'Preset Applied', 'Anti-Packet-Loss (WSS + TLS) applied');
                      }}
                      className={`group p-2.5 rounded-xl border text-left transition-all cursor-pointer flex flex-col justify-between ${
                        formData.transport_type === 'wss' || (formData.transport_type === 'ws' && formData.security_type === 'tls')
                          ? 'bg-emerald-100/80 dark:bg-emerald-900/50 border-emerald-400 dark:border-emerald-500 ring-2 ring-emerald-400/20'
                          : 'bg-white/80 dark:bg-gray-800/80 hover:bg-emerald-50 dark:hover:bg-emerald-950/30 border-gray-200 dark:border-gray-700'
                      }`}
                    >
                      <div className="flex items-center gap-1.5 mb-1">
                        <Rocket size={16} className="text-emerald-500 dark:text-emerald-400" />
                        <span className="text-xs font-bold text-emerald-700 dark:text-emerald-300">Anti-Packet-Loss</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                        WSS + TLS. Rock-solid TCP/WebSocket framing immune to UDP loss & DPI blocking.
                      </p>
                    </button>
                  </div>
                </div>

                {/* Transport & Security Selects */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start pt-1">
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-blue-500"></span>
                        Transport Protocol
                      </label>
                      <span className="text-[11px] font-mono uppercase text-gray-400 dark:text-gray-500">
                        {formData.transport_type}
                      </span>
                    </div>
                    <CustomSelect
                      value={formData.transport_type}
                      onChange={(val) => setFormData({...formData, transport_type: val})}
                      options={[
                        { value: 'tcp', label: 'TCP (Standard)' },
                        { value: 'ws', label: 'WebSocket (WS)' },
                        { value: 'wss', label: 'WebSocket Secure (WSS)' },
                        { value: 'mws', label: 'Multiplex WS (MWS)' },
                        { value: 'mwss', label: 'Multiplex WSS' },
                        { value: 'quic', label: 'QUIC (HTTP/3 UDP, 0-RTT)' },
                        { value: 'grpc', label: 'gRPC (Multiplexed Stealth)' },
                        { value: 'kcp', label: 'KCP (Anti-Packet-Loss ARQ)' },
                        { value: 'ssh', label: 'SSH (Encrypted Subsystem)' },
                      ]}
                    />
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                      {formData.transport_type === 'quic' && 'Fast 0-RTT UDP handshake. Resilient to packet loss.'}
                      {formData.transport_type === 'grpc' && 'Multiplexed HTTP/2. Mimics legitimate enterprise API traffic.'}
                      {formData.transport_type === 'kcp' && 'Aggressive ARQ UDP. Ideal for high packet-loss links.'}
                      {formData.transport_type === 'ws' && 'Standard WebSocket. Compatible with CDN proxies.'}
                      {formData.transport_type === 'mws' && 'Multiplexed WebSocket. Bundles multiple TCP streams.'}
                      {formData.transport_type === 'tcp' && 'Direct raw TCP socket tunnel.'}
                      {formData.transport_type === 'ssh' && 'Native SSH subsystem encrypted protocol.'}
                    </p>
                  </div>

                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-indigo-500"></span>
                        Security / Encryption
                      </label>
                      <span className="text-[11px] font-mono uppercase text-gray-400 dark:text-gray-500">
                        {formData.security_type}
                      </span>
                    </div>
                    <CustomSelect
                      value={formData.security_type}
                      onChange={(val) => setFormData({...formData, security_type: val})}
                      options={[
                        { value: 'none', label: 'None (Plaintext / Low Overhead)' },
                        { value: 'tls', label: 'TLS (Standard Encryption)' },
                        { value: 'utls', label: 'uTLS (Browser Spoofing Anti-DPI)' },
                      ]}
                    />
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
                      {formData.security_type === 'none' && 'No TLS wrapper. Lowest CPU overhead.'}
                      {formData.security_type === 'tls' && 'Standard TLS 1.3 handshake encryption.'}
                      {formData.security_type === 'utls' && 'Camouflages ClientHello to impersonate real browsers.'}
                    </p>
                  </div>
                </div>

                {/* uTLS Fingerprint Card */}
                {formData.security_type === 'utls' && (
                  <div className="p-3.5 rounded-xl bg-indigo-50/60 dark:bg-indigo-950/20 border border-indigo-200/80 dark:border-indigo-900/50 space-y-2">
                    <div className="flex items-center justify-between">
                      <label className="text-xs font-bold text-indigo-900 dark:text-indigo-200 flex items-center gap-1.5">
                        <Fingerprint size={15} className="text-indigo-600 dark:text-indigo-400" />
                        uTLS Client Fingerprint
                      </label>
                      <span className="text-[10px] px-2 py-0.5 rounded-md font-medium bg-indigo-200/60 text-indigo-800 dark:bg-indigo-900/60 dark:text-indigo-300">
                        Anti-DPI Spoofing
                      </span>
                    </div>
                    <p className="text-[11px] text-indigo-700/80 dark:text-indigo-300/80">
                      Replicates exact TLS cipher suites, extensions, and curves of legitimate web browsers:
                    </p>
                    <CustomSelect
                      value={formData.utls_fingerprint || 'chrome'}
                      onChange={(val) => setFormData({...formData, utls_fingerprint: val})}
                      options={[
                        { value: 'chrome', label: 'Google Chrome (Recommended - Highest Compatibility)' },
                        { value: 'firefox', label: 'Mozilla Firefox' },
                        { value: 'ios', label: 'Apple iOS Safari' },
                        { value: 'android', label: 'Android Chrome' },
                        { value: 'edge', label: 'Microsoft Edge' },
                        { value: 'randomized', label: 'Randomized (Rotates browser signature per connection)' },
                      ]}
                    />
                  </div>
                )}

                {/* Failover IPs & Selector Strategy */}
                <div className="p-3.5 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 space-y-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                        <Globe size={15} className="text-blue-600 dark:text-blue-400" />
                        Failover & Additional Foreign Endpoints
                      </label>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400">
                        Add secondary foreign IPs for automatic high-availability failover or load balancing (one per line)
                      </p>
                    </div>
                    {formData.failover_ips && formData.failover_ips.trim().length > 0 && (
                      <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/60 dark:text-blue-300 shrink-0">
                        Multi-Node Active
                      </span>
                    )}
                  </div>
                  <textarea
                    value={formData.failover_ips}
                    onChange={(e) => setFormData({...formData, failover_ips: e.target.value})}
                    className="w-full px-3 py-2 text-sm sm:text-xs font-mono rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500/20"
                    placeholder={"1.2.3.4\n5.6.7.8"}
                    rows={2}
                  />

                  {formData.failover_ips && formData.failover_ips.trim().length > 0 && (
                    <div className="pt-2 border-t border-gray-200/80 dark:border-gray-700/80 space-y-1.5">
                      <div className="flex items-center justify-between">
                        <label className="text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1">
                          <Scale size={14} className="text-blue-500" />
                          Load Balancing / Failover Strategy
                        </label>
                        <span className="text-[11px] font-mono text-gray-400">
                          {formData.selector_strategy || 'fifo'}
                        </span>
                      </div>
                      <CustomSelect
                        value={formData.selector_strategy || 'fifo'}
                        onChange={(val) => setFormData({...formData, selector_strategy: val})}
                        options={[
                          { value: 'fifo', label: 'FIFO Failover (Primary first, fallback to backup IPs)' },
                          { value: 'round', label: 'Round-Robin (Distribute requests evenly across all IPs)' },
                          { value: 'parallel', label: 'Parallel Race (Connect all concurrently, use fastest ping)' },
                          { value: 'rand', label: 'Random (Random distribution across nodes)' },
                        ]}
                      />
                    </div>
                  )}
                </div>

                {/* Mode Switches */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
                  <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                    <div className="pr-2">
                      <div className="flex items-center gap-1.5">
                        <ArrowLeftRight size={15} className="text-blue-600 dark:text-blue-400" />
                        <span className="text-xs font-bold text-gray-900 dark:text-white">Reverse Mode</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                        Foreign connects to Iran (Recommended)
                      </p>
                    </div>
                    <input
                      type="checkbox"
                      className="sr-only peer"
                      checked={formData.is_reverse}
                      onChange={(e) => setFormData({...formData, is_reverse: e.target.checked})}
                    />
                    <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                  </label>

                  <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                    <div className="pr-2">
                      <div className="flex items-center gap-1.5">
                        <Globe size={15} className="text-sky-600 dark:text-sky-400" />
                        <span className="text-xs font-bold text-gray-900 dark:text-white">CDN Mode</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                        Cloudflare / WS proxy
                      </p>
                    </div>
                    <input
                      type="checkbox"
                      className="sr-only peer"
                      checked={formData.cdn_mode}
                      onChange={(e) => {
                        const isChecked = e.target.checked;
                        const updates: any = { cdn_mode: isChecked };
                        if (isChecked && ['tcp', 'udp', 'tcp+udp'].includes(formData.transport_type)) {
                          updates.transport_type = 'ws';
                          showToast('info', 'Transport Switched', 'CDN mode requires WebSocket transport. Auto-switched to WS.')
                        }
                        setFormData({...formData, ...updates});
                      }}
                    />
                    <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                  </label>

                  <label className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
                    <div className="pr-2">
                      <div className="flex items-center gap-1.5">
                        <Gamepad2 size={15} className="text-indigo-600 dark:text-indigo-400" />
                        <span className="text-xs font-bold text-gray-900 dark:text-white">Gaming (Mux)</span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                        Yamux lower ping
                      </p>
                    </div>
                    <input
                      type="checkbox"
                      className="sr-only peer"
                      checked={formData.gaming_mode}
                      onChange={(e) => setFormData({...formData, gaming_mode: e.target.checked})}
                    />
                    <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                  </label>
                </div>

                {/* Security, Limits & KeepAlive Tuning */}
                <div className="pt-3 border-t border-gray-200/80 dark:border-gray-700/80 space-y-3">
                  <div className="flex items-center justify-between">
                    <h5 className="text-xs font-bold uppercase tracking-wider text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                      <ShieldCheck size={15} className="text-gray-700 dark:text-gray-300" />
                      Security & Network Guard
                    </h5>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {/* KeepAlive Interval Card */}
                    <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1">
                          <Activity size={14} className="text-rose-500 dark:text-rose-400" />
                          KeepAlive (Anti-Drop)
                        </label>
                        <span className="text-xs font-mono font-bold text-blue-600 dark:text-blue-400">
                          {formData.keepalive_interval || 15}s
                        </span>
                      </div>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 mb-2">
                        Probe interval to keep stateful NAT firewalls alive
                      </p>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          min="5"
                          max="120"
                          value={formData.keepalive_interval || 15}
                          onChange={(e) => setFormData({...formData, keepalive_interval: parseInt(e.target.value) || 15})}
                          className="w-20 px-2.5 py-1.5 text-xs font-mono font-semibold rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                        />
                        <div className="flex items-center gap-1">
                          {[10, 15, 30].map(sec => (
                            <button
                              key={sec}
                              type="button"
                              onClick={() => setFormData({...formData, keepalive_interval: sec})}
                              className={`text-[10px] font-mono px-2 py-1 rounded-md border transition-all cursor-pointer ${
                                (formData.keepalive_interval || 15) === sec
                                  ? 'bg-blue-600 text-white border-blue-600 font-bold'
                                  : 'bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300 border-gray-200 dark:border-gray-600 hover:bg-gray-100'
                              }`}
                            >
                              {sec}s
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>

                    {/* Stealth Domain Card */}
                    <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
                      <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1 mb-1">
                        <EyeOff size={14} className="text-purple-600 dark:text-purple-400" />
                        Stealth SNI (TLS Spoof)
                      </label>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 mb-2">
                        Mask traffic as legitimate website
                      </p>
                      <input
                        type="text"
                        value={formData.stealth_domain}
                        onChange={(e) => setFormData({...formData, stealth_domain: e.target.value})}
                        className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                        placeholder="e.g. www.google.com"
                      />
                    </div>
                  </div>

                  {/* IP Whitelist & Rate Limit Toggles */}
                  <div className="space-y-2">
                    <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
                      <div className="flex items-center justify-between">
                        <div>
                          <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                            <Shield size={14} className="text-blue-600 dark:text-blue-400" />
                            IP Whitelist (ACL)
                          </label>
                          <p className="text-[11px] text-gray-500 dark:text-gray-400">
                            Restrict tunnel access to specific IP ranges
                          </p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input
                            type="checkbox"
                            className="sr-only peer"
                            checked={formData.allowed_ips_enabled}
                            onChange={(e) => setFormData({...formData, allowed_ips_enabled: e.target.checked})}
                          />
                          <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                        </label>
                      </div>
                      {formData.allowed_ips_enabled && (
                        <div className="mt-2.5 pt-2 border-t border-gray-200/80 dark:border-gray-700/80">
                          <textarea
                            value={formData.allowed_ips}
                            onChange={(e) => setFormData({...formData, allowed_ips: e.target.value})}
                            className="w-full px-2.5 py-1.5 text-xs font-mono rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                            placeholder={"192.168.1.1\n10.0.0.0/24"}
                            rows={2}
                          />
                        </div>
                      )}
                    </div>

                    <div className="p-3 rounded-xl bg-gray-50/80 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
                      <div className="flex items-center justify-between">
                        <div>
                          <label className="text-xs font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                            <Gauge size={14} className="text-amber-600 dark:text-amber-400" />
                            Bandwidth Rate Limit
                          </label>
                          <p className="text-[11px] text-gray-500 dark:text-gray-400">
                            Throttle client speed per connection
                          </p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input
                            type="checkbox"
                            className="sr-only peer"
                            checked={formData.rate_limit_enabled}
                            onChange={(e) => setFormData({...formData, rate_limit_enabled: e.target.checked})}
                          />
                          <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
                        </label>
                      </div>
                      {formData.rate_limit_enabled && (
                        <div className="mt-2.5 pt-2 border-t border-gray-200/80 dark:border-gray-700/80 flex items-center gap-2">
                          <input
                            type="number"
                            value={formData.rate_limit_mbps}
                            onChange={(e) => setFormData({...formData, rate_limit_mbps: e.target.value})}
                            className="w-24 px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                            placeholder="5"
                            min="0.1"
                            step="0.1"
                          />
                          <span className="text-xs font-medium text-gray-600 dark:text-gray-400">Mbps</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                {/* CDN / Stealth / TLS Extra Options */}
                {(formData.cdn_mode || formData.transport_type === 'wss' || formData.transport_type === 'ws' || formData.transport_type === 'grpc' || formData.security_type === 'tls' || formData.security_type === 'utls' || (formData as any).gost_transport === 'wss' || (formData as any).gost_transport === 'ws' || (formData as any).gost_transport === 'grpc' || (formData as any).gost_security === 'tls' || (formData as any).gost_security === 'utls' || formData.type === 'ws' || formData.type === 'grpc') && (
                  <div className="p-3.5 rounded-xl bg-blue-50/60 dark:bg-blue-950/20 border border-blue-200/80 dark:border-blue-900/50 space-y-2.5">
                    <label className="text-xs font-bold text-blue-900 dark:text-blue-200 flex items-center gap-1.5">
                      <Globe size={15} className="text-blue-600 dark:text-blue-400" />
                      Host, SNI & Path Configuration
                    </label>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                      <div>
                        <label className="block text-[11px] font-medium text-gray-700 dark:text-gray-300 mb-1">Custom Host</label>
                        <input type="text" value={formData.custom_host} onChange={(e) => setFormData({...formData, custom_host: e.target.value})} className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white" placeholder="e.g. speedtest.net" />
                      </div>
                      <div>
                        <label className="block text-[11px] font-medium text-gray-700 dark:text-gray-300 mb-1">Custom SNI</label>
                        <input type="text" value={formData.custom_sni} onChange={(e) => setFormData({...formData, custom_sni: e.target.value})} className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white" placeholder="e.g. speedtest.net" />
                      </div>
                      <div>
                        <label className="block text-[11px] font-medium text-gray-700 dark:text-gray-300 mb-1">WS Path</label>
                        <input type="text" value={formData.ws_path} onChange={(e) => setFormData({...formData, ws_path: e.target.value})} className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white" placeholder="/graphql" />
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Pre-flight Diagnostic Box */}
          {isTestingConfig && (
            <div className="p-4 rounded-xl bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 flex items-center gap-3">
              <Loader2 className="animate-spin text-blue-600 dark:text-blue-400 shrink-0" size={20} />
              <span className="text-sm font-medium text-blue-800 dark:text-blue-200">
                Testing node reachability, network ping, and protocol specifications...
              </span>
            </div>
          )}

          {testResult && !isTestingConfig && (
            <div className={`p-4 rounded-xl border transition-all ${
              testResult.valid 
                ? 'bg-emerald-50/70 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-800' 
                : 'bg-amber-50/70 dark:bg-amber-950/30 border-amber-200 dark:border-amber-800'
            }`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  {testResult.valid ? (
                    <CheckCircle2 className="text-emerald-600 dark:text-emerald-400 shrink-0" size={20} />
                  ) : (
                    <AlertTriangle className="text-amber-600 dark:text-amber-400 shrink-0" size={20} />
                  )}
                  <span className="text-sm font-semibold text-gray-900 dark:text-white">
                    {testResult.summary}
                  </span>
                </div>
                {testResult.latency_ms && (
                  <LatencyBadge latency={testResult.latency_ms} status="active" />
                )}
              </div>

              <div className="space-y-2 mt-2">
                {testResult.checks?.map((check: any, idx: number) => (
                  <div key={idx} className="flex items-start gap-2 text-xs">
                    {check.status === 'passed' ? (
                      <CheckCircle2 size={14} className="text-emerald-500 shrink-0 mt-0.5" />
                    ) : check.status === 'warning' ? (
                      <AlertTriangle size={14} className="text-amber-500 shrink-0 mt-0.5" />
                    ) : (
                      <XCircle size={14} className="text-rose-500 shrink-0 mt-0.5" />
                    )}
                    <div className="flex-1">
                      <span className="font-semibold text-gray-800 dark:text-gray-200">{check.title}: </span>
                      <span className="text-gray-600 dark:text-gray-300">{check.detail}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          </div>

          {/* Sticky Footer */}
          <div className="shrink-0 px-4 sm:px-6 py-3.5 border-t border-gray-100 dark:border-gray-700/80 bg-gray-50/90 dark:bg-gray-800/95 backdrop-blur-xs flex items-center justify-between gap-2.5 sm:gap-3">
            <button
              type="button"
              onClick={handleTestConfig}
              disabled={isTestingConfig}
              className="inline-flex items-center gap-2 px-3.5 sm:px-4 py-2 sm:py-2.5 bg-indigo-50 hover:bg-indigo-100 dark:bg-indigo-950/50 dark:hover:bg-indigo-900/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800/80 rounded-xl text-xs sm:text-sm font-semibold transition-all shadow-xs cursor-pointer min-h-[44px] disabled:opacity-50"
            >
              {isTestingConfig ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Zap size={16} />
              )}
              <span className="hidden xs:inline sm:inline">{isTestingConfig ? 'Testing...' : 'Test Connection'}</span>
              <span className="inline xs:hidden sm:hidden">{isTestingConfig ? '...' : 'Test'}</span>
            </button>

            <div className="flex items-center gap-2 sm:gap-3">
              <button
                type="button"
                onClick={onClose}
                className="px-3.5 sm:px-4 py-2 sm:py-2.5 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-600 border border-gray-200 dark:border-gray-600 text-xs sm:text-sm font-semibold transition-all shadow-xs cursor-pointer min-h-[44px]"
              >
                {t.tunnels.cancel}
              </button>
              <button
                type="submit"
                disabled={isTestingConfig}
                className="px-4 sm:px-5 py-2 sm:py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white rounded-xl text-xs sm:text-sm font-semibold transition-all shadow-md shadow-blue-500/20 disabled:opacity-50 cursor-pointer flex items-center gap-1.5 min-h-[44px]"
              >
                Save Changes
              </button>
            </div>
          </div>
        </form>
        <BackhaulAdvancedDrawer
          open={showBackhaulAdvanced}
          state={backhaulAdvanced}
          onClose={() => setShowBackhaulAdvanced(false)}
          onChange={setBackhaulAdvanced}
        />
      </div>
    </div>
  )
}

export default EditTunnelModal

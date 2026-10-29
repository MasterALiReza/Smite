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
import { GostSection } from './sections/GostSection'
import { RatholeSection } from './sections/RatholeSection'
import { ChiselSection } from './sections/ChiselSection'
import { FrpSection } from './sections/FrpSection'
import { BackhaulSection } from './sections/BackhaulSection'
import {
  BackhaulForm, BackhaulAdvancedDrawer, buildBackhaulSpec, parseBackhaulSpec
} from './BackhaulComponents'
import {
  Tunnel, TunnelCategory, BackhaulTransport, BackhaulFormState,
  BackhaulAdvancedState, BackhaulAdvancedServerState, BackhaulAdvancedClientState,
  generateRandomControlPort, createDefaultBackhaulState, createDefaultBackhaulAdvancedState, resetGamingAdvanced,
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
    frp_sni: (tunnel.custom_sni || tunnel.spec?.custom_sni || tunnel.stealth_domain || tunnel.spec?.stealth_domain || '').trim(),
    frp_encryption: tunnel.spec?.use_encryption !== false,
    frp_compression: tunnel.spec?.use_compression !== false,
    frp_health_check: tunnel.spec?.enable_health_check !== false && tunnel.spec?.health_check_type !== null,
    frp_bandwidth_limit: tunnel.spec?.bandwidth_limit || '',
    frp_proxy_protocol: tunnel.spec?.proxy_protocol_version || 'none',
    frp_custom_domains: Array.isArray(tunnel.spec?.custom_domains) ? tunnel.spec.custom_domains.join(', ') : (tunnel.spec?.custom_domains || ''),
    use_ipv6: Boolean(tunnel.spec?.use_ipv6),
    node_ipv6: tunnel.spec?.node_ipv6 || '',
    cdn_mode: tunnel.cdn_mode || false,
    gaming_mode: tunnel.gaming_mode || false,
    custom_host: tunnel.custom_host || tunnel.spec?.custom_host || '',
    custom_sni: (tunnel.custom_sni || tunnel.spec?.custom_sni || tunnel.stealth_domain || tunnel.spec?.stealth_domain || '').trim(),
    ws_path: tunnel.ws_path || tunnel.spec?.ws_path || '',
    is_reverse: tunnel.is_reverse !== undefined && tunnel.is_reverse !== null
      ? Boolean(tunnel.is_reverse)
      : (tunnel.spec?.is_reverse !== undefined && tunnel.spec?.is_reverse !== null
          ? Boolean(tunnel.spec.is_reverse)
          : !tunnel.spec?.force_direct),
    stealth_domain: (tunnel.stealth_domain || tunnel.spec?.stealth_domain || tunnel.custom_sni || tunnel.spec?.custom_sni || '').trim(),
    transport_type: tunnel.transport_type || 'tcp',
    security_type: tunnel.security_type === 'utls' ? 'tls' : (tunnel.security_type || 'none'),
    selector_strategy: tunnel.selector_strategy || 'fifo',
    utls_fingerprint: tunnel.utls_fingerprint || 'chrome',
    keepalive_interval: tunnel.keepalive_interval || 15,
    failover_ips: tunnel.failover_ips && Array.isArray(tunnel.failover_ips) ? tunnel.failover_ips.join('\n') : '',
    rate_limit_mbps: (tunnel.rate_limit_mbps ?? tunnel.spec?.rate_limit_mbps) ? String(tunnel.rate_limit_mbps ?? tunnel.spec?.rate_limit_mbps) : '',
    allowed_ips: tunnel.allowed_ips && Array.isArray(tunnel.allowed_ips) ? tunnel.allowed_ips.join('\n') : '',
    rate_limit_enabled: !!(tunnel.rate_limit_mbps || tunnel.spec?.rate_limit_mbps),
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
      let updatedSpec: Record<string, any> = { ...tunnel.spec, use_ipv6: formData.use_ipv6 }
      
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
        if (formData.custom_sni && formData.custom_sni.trim()) {
          updatedSpec.custom_sni = formData.custom_sni.trim()
          updatedSpec.stealth_domain = formData.custom_sni.trim()
        } else {
          delete updatedSpec.custom_sni
          delete updatedSpec.stealth_domain
        }
        updatedSpec.ports = ports
        updatedSpec.remote_port = ports[0]  // Keep for backward compatibility
        updatedSpec.listen_port = ports[0]  // Keep for backward compatibility
      } else if (tunnel.core === 'gost') {
        const remoteIp = formData.remote_ip || '127.0.0.1'
        const selectedType = formData.type || tunnel.type || 'tcp'
        updatedSpec.type = selectedType
        updatedSpec.tunnel_type = selectedType
        updatedSpec.remote_ip = remoteIp
        updatedSpec.target_host = remoteIp
        updatedSpec.ports = ports
        updatedSpec.port_ranges = port_ranges
        const fallbackPort = ports.length > 0 ? ports[0] : (port_ranges.length > 0 ? parseInt(port_ranges[0].split('-')[0]) : 8080)
        updatedSpec.remote_port = fallbackPort  // Keep for backward compatibility
        updatedSpec.listen_port = fallbackPort  // Keep for backward compatibility
        updatedSpec.is_reverse = Boolean(formData.is_reverse)
        updatedSpec.force_direct = !formData.is_reverse
        if (formData.rate_limit_enabled && formData.rate_limit_mbps && parseFloat(formData.rate_limit_mbps) > 0) {
          updatedSpec.rate_limit_mbps = parseFloat(formData.rate_limit_mbps)
        } else {
          delete updatedSpec.rate_limit_mbps
        }
        const effectiveGostSni = (formData.custom_sni || formData.stealth_domain || '').trim()
        if (effectiveGostSni) {
          updatedSpec.stealth_domain = effectiveGostSni
          updatedSpec.custom_sni = effectiveGostSni
        } else {
          delete updatedSpec.stealth_domain
          delete updatedSpec.custom_sni
        }
        if (formData.custom_host && formData.custom_host.trim()) {
          updatedSpec.custom_host = formData.custom_host.trim()
        } else {
          delete updatedSpec.custom_host
        }
        if (formData.ws_path && formData.ws_path.trim()) {
          updatedSpec.ws_path = formData.ws_path.trim()
        } else {
          delete updatedSpec.ws_path
        }
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
        updatedSpec.security_type = formData.frp_transport === 'kcp' ? 'none' : (formData.frp_security || 'tls')
        if (formData.frp_sni && formData.frp_sni.trim()) {
          updatedSpec.custom_sni = formData.frp_sni.trim()
          updatedSpec.stealth_domain = formData.frp_sni.trim()
        } else {
          delete updatedSpec.custom_sni
          delete updatedSpec.stealth_domain
        }
        updatedSpec.use_encryption = formData.frp_encryption
        updatedSpec.use_compression = formData.frp_compression
        updatedSpec.enable_health_check = formData.frp_health_check !== false
        updatedSpec.health_check_type = formData.frp_health_check !== false ? (tunnel.type === 'http' ? 'http' : 'tcp') : null
        if (formData.frp_bandwidth_limit && formData.frp_bandwidth_limit.trim()) {
          updatedSpec.bandwidth_limit = formData.frp_bandwidth_limit.trim()
        } else {
          delete updatedSpec.bandwidth_limit
          delete updatedSpec.rate_limit_mbps
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
        const bhSniVal = (formData.custom_sni || '').trim()
        if (bhSniVal) {
          updatedSpec.custom_sni = bhSniVal
          updatedSpec.stealth_domain = bhSniVal
        } else {
          delete updatedSpec.custom_sni
          delete updatedSpec.stealth_domain
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
        ...(tunnel.core === 'rathole' && {
          custom_sni: (formData.custom_sni && formData.custom_sni.trim()) ? formData.custom_sni.trim() : null,
          stealth_domain: (formData.custom_sni && formData.custom_sni.trim()) ? formData.custom_sni.trim() : null,
        }),
        ...(tunnel.core === 'backhaul' && {
          type: (backhaulState.accept_udp ? (formData.type === 'udp' ? 'udp' : 'tcp+udp') : 'tcp'),
          security_type: (backhaulState.transport === 'wss' || backhaulState.transport === 'wssmux') ? 'tls' : 'none',
          custom_sni: (formData.custom_sni || '').trim() || null,
          stealth_domain: (formData.custom_sni || '').trim() || null,
          gaming_mode: Boolean(backhaulState.gaming_mode),
          is_reverse: true,
          port_ranges: port_ranges.length > 0 ? port_ranges : null,
        }),
        ...(tunnel.core === 'frp' && {
          transport_type: formData.frp_transport || 'tcp',
          security_type: formData.frp_transport === 'kcp' ? 'none' : (formData.frp_security || 'tls'),
          custom_sni: (formData.frp_sni && formData.frp_sni.trim()) ? formData.frp_sni.trim() : null,
          stealth_domain: (formData.frp_sni && formData.frp_sni.trim()) ? formData.frp_sni.trim() : null,
          is_reverse: Boolean(formData.is_reverse),
          rate_limit_mbps: null,
        }),
        ...(tunnel.core === 'chisel' && {
          security_type: formData.chisel_transport === 'wss' ? 'tls' : 'none',
          custom_sni: (formData.chisel_custom_sni && formData.chisel_custom_sni.trim()) ? formData.chisel_custom_sni.trim() : null,
          stealth_domain: (formData.chisel_custom_sni && formData.chisel_custom_sni.trim()) ? formData.chisel_custom_sni.trim() : null,
          custom_host: (formData.chisel_custom_host && formData.chisel_custom_host.trim()) ? formData.chisel_custom_host.trim() : null,
          is_reverse: Boolean(formData.is_reverse),
          port_ranges: port_ranges.length > 0 ? port_ranges : null,
        }),
        ...(tunnel.core === 'gost' && {
          type: formData.type || tunnel.type || 'tcp',
          transport_type: formData.transport_type,
          cdn_mode: formData.cdn_mode,
          gaming_mode: formData.gaming_mode,
          custom_host: (formData.custom_host && formData.custom_host.trim()) ? formData.custom_host.trim() : null,
          custom_sni: effectiveGostSni || null,
          ws_path: (formData.ws_path && formData.ws_path.trim()) ? formData.ws_path.trim() : null,
          is_reverse: Boolean(formData.is_reverse),
          stealth_domain: effectiveGostSni || null,
          security_type: formData.security_type,
          selector_strategy: formData.selector_strategy || 'fifo',
          utls_fingerprint: formData.security_type === 'utls' ? (formData.utls_fingerprint || 'chrome') : null,
          keepalive_interval: formData.keepalive_interval ? parseInt(String(formData.keepalive_interval)) : 15,
          failover_ips: formData.failover_ips ? formData.failover_ips.split('\n').map((ip: string) => ip.trim()).filter((ip: string) => ip.length > 0) : null,
          rate_limit_mbps: formData.rate_limit_enabled && formData.rate_limit_mbps && parseFloat(formData.rate_limit_mbps) > 0 ? parseFloat(formData.rate_limit_mbps) : null,
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
                    {/* Dynamic Core Configuration Sections */}
          {tunnel.core === 'gost' && (
            <div className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                  {t.tunnels.type}
                </label>
                <CustomSelect
                  value={formData.type || tunnel.type || 'tcp'}
                  onChange={(val) => setFormData((prev) => ({ ...prev, type: val as string }))}
                  options={[
                    { value: 'tcp', label: 'TCP (Standard)' },
                    { value: 'udp', label: 'UDP (Gaming / Anti-Lag)' },
                    { value: 'tcp+udp', label: 'TCP + UDP (Dual Forward)' },
                  ]}
                />
              </div>
              <GostSection
                formData={formData}
                onChange={(updates) => setFormData((prev) => ({ ...prev, ...updates }))}
                showToast={showToast}
              />
            </div>
          )}

          {tunnel.core === 'backhaul' && (
            <div>
              <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                {t.tunnels.type}
              </label>
              <CustomSelect
                value={formData.type || (backhaulState.accept_udp ? 'tcp+udp' : 'tcp')}
                onChange={(val) => {
                  const value = val as string
                  if (value === 'udp' || value === 'tcp+udp') {
                    setFormData((prev) => ({ ...prev, type: value, transport_type: 'tcp' }))
                    setBackhaulState((prev) => ({ ...prev, accept_udp: true, transport: 'tcp' }))
                  } else {
                    setFormData((prev) => ({ ...prev, type: 'tcp' }))
                    setBackhaulState((prev) => ({ ...prev, accept_udp: false }))
                  }
                }}
                options={[
                  { value: 'tcp', label: 'TCP (Standard)' },
                  { value: 'udp', label: 'UDP (Gaming / Anti-Lag)' },
                  { value: 'tcp+udp', label: 'TCP + UDP (Dual Forward)' },
                ]}
              />
            </div>
          )}

          {tunnel.core === 'backhaul' && (
            <BackhaulSection
              state={backhaulState}
              advancedState={backhaulAdvanced}
              onChange={(partial) => {
                setBackhaulState((prev) => ({ ...prev, ...partial }))
                if (partial.transport) {
                  setFormData((prev) => ({ ...prev, transport_type: partial.transport as string }))
                }
                if (
                  partial.transport &&
                  partial.transport !== 'tcp' &&
                  partial.transport !== 'udp' &&
                  partial.accept_udp !== true
                ) {
                  // other transports ignore accept_udp upstream: keep the tunnel TCP-only
                  setBackhaulState((prev) => ({ ...prev, accept_udp: false }))
                  setFormData((prev) => ({ ...prev, type: 'tcp' }))
                }
                if (partial.accept_udp !== undefined) {
                  if (partial.accept_udp && formData.type === 'tcp') {
                    setFormData((prev) => ({ ...prev, type: 'tcp+udp' }))
                  } else if (!partial.accept_udp && formData.type !== 'tcp') {
                    setFormData((prev) => ({ ...prev, type: 'tcp' }))
                  }
                }
                if (partial.gaming_mode !== undefined) {
                  setFormData((prev: any) => ({ ...prev, gaming_mode: Boolean(partial.gaming_mode) }))
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
                      },
                      client: {
                        ...prev.client,
                        nodelay: true,
                        channel_size: '8192',
                        mux_framesize: '4096',
                        mss: '1380',
                        keepalive_period: '12',
                        heartbeat: '12',
                        aggressive_pool: true,
                      },
                    }))
                  } else {
                    // Gaming Mode off: restore standard tuning values
                    setBackhaulAdvanced(resetGamingAdvanced)
                  }
                }
              }}
              onAdvancedChange={setBackhaulAdvanced}
              onOpenAdvanced={() => setShowBackhaulAdvanced(true)}
              acceptUdpVisible={
                backhaulState.transport === 'tcp' ||
                backhaulState.transport === 'udp'
              }
              showToast={showToast}
            />
          )}

          {tunnel.core === 'rathole' && (
            <RatholeSection
              formData={formData}
              onChange={(updates) => setFormData((prev) => ({ ...prev, ...updates }))}
              showToast={showToast}
            />
          )}

          {tunnel.core === 'chisel' && (
            <ChiselSection
              formData={formData}
              onChange={(updates) => setFormData((prev) => ({ ...prev, ...updates }))}
              showToast={showToast}
            />
          )}

          {tunnel.core === 'frp' && (
            <FrpSection
              formData={formData}
              onChange={(updates) => setFormData((prev) => ({ ...prev, ...updates }))}
              showToast={showToast}
            />
          )}

          {/* v4 to v6 tunnel toggle card - only for Rathole, Backhaul, Chisel, FRP (not GOST) */}
          {tunnel.core !== 'gost' && (
            <label className="p-3.5 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 flex items-center justify-between cursor-pointer hover:bg-gray-100/70 dark:hover:bg-gray-700/40 transition-all">
              <div className="pr-2">
                <div className="flex items-center gap-2">
                  <Globe size={15} className="text-blue-600 dark:text-blue-400" />
                  <span className="text-xs font-bold text-gray-900 dark:text-white">v4 to v6 Tunnel</span>
                </div>
                <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
                  Listen on IPv4 (Iran node) and forward to target node via IPv6.
                </p>
              </div>
              <input
                type="checkbox"
                id="v4_to_v6"
                className="sr-only peer"
                checked={formData.use_ipv6}
                onChange={(e) => setFormData({ ...formData, use_ipv6: e.target.checked })}
              />
              <div className="w-9 h-5 bg-gray-300 dark:bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 shrink-0 relative"></div>
            </label>
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

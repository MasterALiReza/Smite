import React, { useState, useEffect } from 'react'
import { 
  X, Loader2, AlertTriangle, ShieldCheck, Gamepad2, Settings2, Sliders, Globe,
  Network, Cpu, Radio, Key, Lock, EyeOff, Gauge, Server, Terminal, RefreshCw, Zap,
  Dices, ArrowLeftRight, Sparkles, Rocket, Fingerprint, Scale, RadioTower, Wifi, Info,
  FolderPlus, CheckCircle2, XCircle, Activity, Plus, Tag, FolderMinus, Shield, Layers
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
  generateRandomControlPort, createDefaultBackhaulState, createDefaultBackhaulAdvancedState,
  numericServerKeys, booleanServerKeys, stringServerKeys,
  numericClientKeys, booleanClientKeys, stringClientKeys, getCategoryColorClasses
} from './types'

interface AddTunnelModalProps {
  nodes: any[]
  servers: any[]
  categories?: TunnelCategory[]
  onCategoryCreated?: (name: string, color?: string) => Promise<any>
  onClose: () => void
  onSuccess: () => void
}

const AddTunnelModal = ({ nodes, servers, categories = [], onCategoryCreated, onClose, onSuccess }: AddTunnelModalProps) => {
  const { t } = useLanguage()
  const { showToast } = useToast()
  const [formData, setFormData] = useState({
    name: '',
    core: 'gost',
    type: 'tcp',
    node_id: '',
    foreign_node_id: '',
    iran_node_id: '',
    ports: '8080',  // Comma-separated ports (e.g., "8080,8081,8082")
    remote_ip: '127.0.0.1',
    rathole_remote_addr: generateRandomControlPort(),
    rathole_token: '',
    rathole_transport: 'tcp',
    chisel_control_port: generateRandomControlPort(),
    chisel_transport: 'ws',
    chisel_backend_url: 'https://speedtest.net',
    chisel_custom_sni: '',
    chisel_custom_host: '',
    chisel_keepalive: '10s',
    frp_bind_port: generateRandomControlPort(),
    frp_token: '',
    frp_local_ip: '127.0.0.1',
    frp_transport: 'tcp',
    frp_security: 'tls',
    frp_sni: '',
    frp_encryption: true,
    frp_compression: true,
    frp_health_check: true,
    frp_bandwidth_limit: '',
    frp_proxy_protocol: 'none',
    frp_custom_domains: '',
    use_ipv6: false,
    node_ipv6: '',  // Optional IPv6 address for node (Rathole/Chisel)
    spec: {} as Record<string, any>,
    cdn_mode: false,
    gaming_mode: false,
    custom_host: '',
    custom_sni: '',
    ws_path: '',
    is_reverse: true,
    stealth_domain: '',
    transport_type: 'tcp',
    security_type: 'none',
    selector_strategy: 'fifo',
    utls_fingerprint: 'chrome',
    keepalive_interval: 15,
    failover_ips: '',
    rate_limit_mbps: '',
    allowed_ips: '',
    rate_limit_enabled: false,
    allowed_ips_enabled: false,
    category: '',
  })
  const [showInlineNewCategory, setShowInlineNewCategory] = useState(false)
  const [inlineCategoryName, setInlineCategoryName] = useState('')
  const [backhaulState, setBackhaulState] = useState<BackhaulFormState>(createDefaultBackhaulState())
  const [backhaulAdvanced, setBackhaulAdvanced] = useState<BackhaulAdvancedState>(createDefaultBackhaulAdvancedState())
  const [showBackhaulAdvanced, setShowBackhaulAdvanced] = useState(false)
  const [isTestingConfig, setIsTestingConfig] = useState(false)
  const [testResult, setTestResult] = useState<any | null>(null)

  const handleTestConfig = async () => {
    setIsTestingConfig(true)
    setTestResult(null)
    try {
      const payload: any = {
        core: formData.core,
        iran_node_id: formData.iran_node_id || formData.node_id,
        foreign_node_id: formData.foreign_node_id,
        ports: formData.core === 'backhaul' ? ((backhaulAdvanced as any).port_ranges ? `${backhaulState.public_port},${(backhaulAdvanced as any).port_ranges}` : backhaulState.public_port) : formData.ports,
        control_port: formData.core === 'backhaul' ? ((backhaulAdvanced as any).control_port || backhaulState.control_port) : (formData.core === 'rathole' ? formData.rathole_remote_addr : (formData.core === 'frp' ? formData.frp_bind_port : (formData.core === 'chisel' ? formData.chisel_control_port : undefined))),
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
        transport: formData.core === 'backhaul' ? backhaulState.transport : (formData.core === 'rathole' ? formData.rathole_transport : (formData.core === 'chisel' ? formData.chisel_transport : (formData.core === 'frp' ? formData.frp_transport : formData.transport_type))),
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

  // Auto-populate remote_ip with foreign server IP when GOST is selected
  useEffect(() => {
    if (formData.core === 'gost' && formData.foreign_node_id) {
      const selectedServer = servers.find(s => s.id === formData.foreign_node_id)
      if (selectedServer?.metadata?.ip_address) {
        setFormData(prev => ({
          ...prev,
          remote_ip: selectedServer.metadata.ip_address
        }))
      }
    }
  }, [formData.foreign_node_id, formData.core, servers])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      let spec = getSpecForType(formData.core, formData.type)
      let tunnelType = formData.type
      
      spec.use_ipv6 = formData.use_ipv6 || false
      
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
      
      if (formData.core === 'gost') {
        const parsed = parsePortsAndRanges(formData.ports)
        ports = parsed.ports
        port_ranges = parsed.port_ranges
        if (ports.length === 0 && port_ranges.length === 0) {
          showToast('warning', 'Invalid Ports', 'Please enter at least one valid port or port range')
          return
        }
      } else if (formData.core === 'backhaul') {
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
      
      if (formData.core === 'gost' && (formData.type === 'tcp' || formData.type === 'udp' || formData.type === 'tcp+udp' || formData.type === 'grpc' || formData.type === 'tcpmux')) {
        const remoteIp = formData.remote_ip || (formData.use_ipv6 ? '::1' : '127.0.0.1')
        // For GOST, ports are equal (listen_port = forward_to port)
        spec.remote_ip = remoteIp
        spec.ports = ports  // Store multiple ports
        spec.port_ranges = port_ranges
        const fallbackPort = ports.length > 0 ? ports[0] : (port_ranges.length > 0 ? parseInt(port_ranges[0].split('-')[0]) : 8080)
        spec.listen_port = fallbackPort  // Keep first port for backward compatibility
        spec.remote_port = fallbackPort  // Keep first port for backward compatibility
        spec.is_reverse = Boolean(formData.is_reverse)
        spec.force_direct = !formData.is_reverse
        if (formData.rate_limit_enabled && formData.rate_limit_mbps && parseFloat(formData.rate_limit_mbps) > 0) {
          spec.rate_limit_mbps = parseFloat(formData.rate_limit_mbps)
        } else {
          delete spec.rate_limit_mbps
        }
        if (formData.stealth_domain && formData.stealth_domain.trim()) {
          spec.stealth_domain = formData.stealth_domain.trim()
        } else {
          delete spec.stealth_domain
        }
        if (formData.custom_sni && formData.custom_sni.trim()) {
          spec.custom_sni = formData.custom_sni.trim()
        } else {
          delete spec.custom_sni
        }
        if (formData.custom_host && formData.custom_host.trim()) {
          spec.custom_host = formData.custom_host.trim()
        } else {
          delete spec.custom_host
        }
        if (formData.ws_path && formData.ws_path.trim()) {
          spec.ws_path = formData.ws_path.trim()
        } else {
          delete spec.ws_path
        }
      }
      
      if (formData.core === 'rathole') {
        const selectedIran = nodes.find(n => n.id === (formData.iran_node_id || formData.node_id))
        const remoteHost = selectedIran?.ip_address || selectedIran?.metadata?.ip_address || window.location.hostname
        const remotePort = formData.rathole_remote_addr || String(Math.floor(25000 + Math.random() * 25000))
        spec.remote_addr = `${remoteHost}:${remotePort}`
        spec.control_port = parseInt(remotePort)
        if (formData.rathole_token) {
          spec.token = formData.rathole_token
        }
        spec.ports = ports
        spec.remote_port = ports[0]
        spec.listen_port = ports[0]
        spec.transport = formData.rathole_transport || 'tcp'
        spec.transport_type = formData.rathole_transport || 'tcp'
        if (formData.custom_sni && formData.custom_sni.trim()) {
          spec.custom_sni = formData.custom_sni.trim()
          spec.stealth_domain = formData.custom_sni.trim()
        } else {
          delete spec.custom_sni
          delete spec.stealth_domain
        }
      }
      
      if (formData.core === 'chisel') {
        // For Chisel, ports are equal (reverse_port = local_port)
        spec.ports = ports  // Store multiple ports
        spec.port_ranges = port_ranges
        spec.is_reverse = formData.is_reverse !== false
        spec.force_direct = formData.is_reverse === false
        const firstPort = ports[0]
        spec.listen_port = firstPort
        spec.remote_port = firstPort
        spec.server_port = firstPort
        const controlPort = formData.chisel_control_port 
          ? parseInt(formData.chisel_control_port.toString())
          : firstPort + 10000
        spec.control_port = controlPort
        spec.transport = formData.chisel_transport || 'ws'
        spec.transport_type = formData.chisel_transport || 'ws'
        if (formData.chisel_backend_url) {
          spec.backend_url = formData.chisel_backend_url
        }
        if (formData.chisel_custom_sni && formData.chisel_custom_sni.trim()) {
          spec.custom_sni = formData.chisel_custom_sni.trim()
          spec.stealth_domain = formData.chisel_custom_sni.trim()
        } else {
          delete spec.custom_sni
          delete spec.stealth_domain
        }
        if (formData.chisel_custom_host) {
          spec.custom_host = formData.chisel_custom_host
        }
        if (formData.chisel_keepalive) {
          spec.keepalive = formData.chisel_keepalive
        }
        const panelHost = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
        spec.panel_host = panelHost
      }
      
      if (formData.core === 'backhaul') {
        if (!formData.node_id) {
          showToast('warning', 'Node Required', 'Backhaul tunnels require an Iran node')
          return
        }
        // CRITICAL: For Backhaul, the Ports field is in BackhaulForm, not in the main formData.ports
        // backhaulState.public_port contains the comma-separated ports from the Backhaul form
        // We should use backhaulState.public_port, NOT formData.ports (which is for other cores)
        console.log('Backhaul tunnel creation - formData.ports:', formData.ports, 'type:', typeof formData.ports)
        console.log('Backhaul tunnel creation - backhaulState.public_port:', backhaulState.public_port)
        
        // Use backhaulState.public_port (from BackhaulForm) - it has the correct comma-separated ports
        // Only fallback to formData.ports if backhaulState.public_port is empty
        const portsToUse = backhaulState.public_port && backhaulState.public_port.trim() 
          ? backhaulState.public_port 
          : (formData.ports || '8080')
        
        const updatedBackhaulState = {
          ...backhaulState,
          public_port: portsToUse,
          target_port: portsToUse
        }
        console.log('Backhaul tunnel creation - updatedBackhaulState.public_port (final):', updatedBackhaulState.public_port)
        spec = buildBackhaulSpec(updatedBackhaulState, backhaulAdvanced)
        spec.use_ipv6 = formData.use_ipv6 || false
        // buildBackhaulSpec should already build ports array from public_port (formData.ports)
        // Verify ports were built correctly - if not, build them from parsed ports
        if (!spec.ports || !Array.isArray(spec.ports) || spec.ports.length === 0) {
          // buildBackhaulSpec didn't build ports, so build them from formData.ports
          if (backhaulAdvanced.customPorts && backhaulAdvanced.customPorts.trim()) {
            // Use customPorts if provided
            spec.ports = backhaulAdvanced.customPorts
              .split(/\r?\n/)
              .map((line) => line.trim())
              .filter(Boolean)
          } else if (ports.length > 0) {
            // Build from parsed ports (numbers) - format: "port=targetHost:port"
            const targetHost = spec.target_host || '127.0.0.1'
            const listenIp = spec.listen_ip || updatedBackhaulState.listen_ip || '0.0.0.0'
            spec.ports = ports.map(p => {
              // Format: "port=targetHost:port" or "listenIp:port=targetHost:port" if listenIp is set
              const listenPart = listenIp !== '0.0.0.0' ? `${listenIp}:${p}` : `${p}`
              return `${listenPart}=${targetHost}:${p}`
            })
          }
        }
        // Ensure ports array is properly formatted and has all ports
        if (spec.ports && Array.isArray(spec.ports) && spec.ports.length > 0) {
          console.log('Backhaul tunnel creation - final ports:', spec.ports, 'count:', spec.ports.length)
        } else {
          console.warn('Backhaul tunnel creation - no ports found! formData.ports:', formData.ports, 'publicPorts:', updatedBackhaulState.public_port)
        }
        tunnelType = (formData.type === 'udp' || formData.type === 'tcp+udp') ? formData.type : 'tcp'
        spec.transport = backhaulState.transport || 'tcpmux'
        spec.transport_type = backhaulState.transport || 'tcpmux'
        spec.type = tunnelType
      }
      
      if (formData.core === 'frp') {
        if (!formData.node_id && !formData.iran_node_id) {
          showToast('warning', 'Node Required', 'FRP tunnels require an Iran node')
          return
        }
        const bindPort = parseInt(formData.frp_bind_port) || 7000
        spec.bind_port = bindPort
        spec.ports = ports
        spec.listen_port = ports[0]
        spec.remote_port = ports[0]
        if (formData.frp_token) {
          spec.token = formData.frp_token
        }
        spec.local_ip = formData.frp_local_ip || '127.0.0.1'
        spec.local_port = ports[0]
        spec.type = (formData.type === 'udp' || formData.type === 'tcp+udp' || formData.type === 'http' || formData.type === 'https') ? formData.type : 'tcp'
        tunnelType = spec.type
        spec.transport_type = formData.frp_transport || 'tcp'
        spec.transport = formData.frp_transport || 'tcp'
        spec.security_type = formData.frp_security || 'tls'
        if (formData.frp_sni && formData.frp_sni.trim()) {
          spec.custom_sni = formData.frp_sni.trim()
          spec.stealth_domain = formData.frp_sni.trim()
        } else {
          delete spec.custom_sni
          delete spec.stealth_domain
        }
        spec.use_encryption = formData.frp_encryption
        spec.use_compression = formData.frp_compression
        spec.enable_health_check = formData.frp_health_check !== false
        spec.health_check_type = formData.frp_health_check !== false ? (formData.type === 'http' ? 'http' : 'tcp') : null
        if (formData.frp_bandwidth_limit) {
          spec.bandwidth_limit = formData.frp_bandwidth_limit
        }
        if (formData.frp_proxy_protocol && formData.frp_proxy_protocol !== 'none') {
          spec.proxy_protocol_version = formData.frp_proxy_protocol
        }
        if (formData.frp_custom_domains) {
          spec.custom_domains = formData.frp_custom_domains.split(',').map((d: string) => d.trim()).filter(Boolean)
        }
        spec.is_reverse = formData.is_reverse !== false
        spec.force_direct = formData.is_reverse === false
      }
      
      if (formData.core === 'gost' && formData.ws_path && !formData.ws_path.startsWith('/')) {
        showToast('warning', 'Validation', 'WS Path must start with a slash (e.g., /graphql)')
        return
      }

      const payload = {
        name: formData.name,
        core: formData.core,
        type: tunnelType,
        spec: spec,
        gaming_mode: Boolean(formData.gaming_mode || spec?.gaming_mode),
        ...(formData.core === 'gost' && {
          cdn_mode: formData.cdn_mode,
          gaming_mode: formData.gaming_mode,
          custom_host: (formData.custom_host && formData.custom_host.trim()) ? formData.custom_host.trim() : null,
          custom_sni: (formData.custom_sni && formData.custom_sni.trim()) ? formData.custom_sni.trim() : null,
          ws_path: (formData.ws_path && formData.ws_path.trim()) ? formData.ws_path.trim() : null,
          is_reverse: Boolean(formData.is_reverse),
          stealth_domain: (formData.stealth_domain && formData.stealth_domain.trim()) ? formData.stealth_domain.trim() : null,
          transport_type: formData.transport_type,
          security_type: formData.security_type,
          selector_strategy: formData.selector_strategy || 'fifo',
          utls_fingerprint: formData.security_type === 'utls' ? (formData.utls_fingerprint || 'chrome') : null,
          keepalive_interval: formData.keepalive_interval ? parseInt(String(formData.keepalive_interval)) : 15,
          failover_ips: formData.failover_ips ? formData.failover_ips.split('\n').map(ip => ip.trim()).filter(ip => ip.length > 0) : null,
          rate_limit_mbps: formData.rate_limit_enabled && formData.rate_limit_mbps && parseFloat(formData.rate_limit_mbps) > 0 ? parseFloat(formData.rate_limit_mbps) : null,
          allowed_ips: formData.allowed_ips_enabled && formData.allowed_ips 
            ? formData.allowed_ips.split('\n').map(ip => ip.trim()).filter(ip => ip.length > 0)
            : null,
          port_ranges: port_ranges.length > 0 ? port_ranges : null
        }),
        ...(formData.core === 'frp' && {
          transport_type: formData.frp_transport || 'tcp',
          security_type: formData.frp_security || 'tls',
          custom_sni: (formData.frp_sni && formData.frp_sni.trim()) ? formData.frp_sni.trim() : null,
          stealth_domain: (formData.frp_sni && formData.frp_sni.trim()) ? formData.frp_sni.trim() : null,
          is_reverse: formData.is_reverse !== false,
        }),
        ...(formData.core === 'chisel' && {
          transport_type: formData.chisel_transport || 'ws',
          security_type: formData.chisel_transport === 'wss' ? 'tls' : 'none',
          custom_sni: (formData.chisel_custom_sni && formData.chisel_custom_sni.trim()) ? formData.chisel_custom_sni.trim() : null,
          stealth_domain: (formData.chisel_custom_sni && formData.chisel_custom_sni.trim()) ? formData.chisel_custom_sni.trim() : null,
          custom_host: (formData.chisel_custom_host && formData.chisel_custom_host.trim()) ? formData.chisel_custom_host.trim() : null,
          is_reverse: formData.is_reverse !== false,
          port_ranges: port_ranges.length > 0 ? port_ranges : null,
        }),
        ...(formData.core === 'rathole' && {
          transport_type: formData.rathole_transport || 'tcp',
          custom_sni: (formData.custom_sni && formData.custom_sni.trim()) ? formData.custom_sni.trim() : null,
          stealth_domain: (formData.custom_sni && formData.custom_sni.trim()) ? formData.custom_sni.trim() : null,
          is_reverse: true,
        }),
        ...(formData.core === 'backhaul' && {
          transport_type: backhaulState.transport || 'tcpmux',
          security_type: (backhaulState.transport === 'wss' || backhaulState.transport === 'wssmux') ? 'tls' : 'none',
          custom_sni: ((backhaulAdvanced as any).sni || (formData.custom_sni && formData.custom_sni.trim()) || '').trim() || null,
          stealth_domain: ((backhaulAdvanced as any).sni || (formData.custom_sni && formData.custom_sni.trim()) || '').trim() || null,
          gaming_mode: backhaulState.gaming_mode || backhaulAdvanced.server.nodelay || false,
          port_ranges: ((backhaulAdvanced as any).port_ranges && (backhaulAdvanced as any).port_ranges.trim())
            ? (backhaulAdvanced as any).port_ranges.split(',').map((s: string) => s.trim()).filter(Boolean)
            : (port_ranges.length > 0 ? port_ranges : null),
          is_reverse: true,
        }),
        node_id: (formData.is_reverse !== false) ? (formData.iran_node_id || formData.node_id) : (formData.foreign_node_id || formData.node_id),
        foreign_node_id: formData.foreign_node_id || null,
        iran_node_id: formData.iran_node_id || formData.node_id || null,
        category: formData.category ? formData.category.trim() : null
      }
      await api.post('/tunnels', payload)
      showToast('success', 'Tunnel Created', `${formData.name} was created successfully`)
      onSuccess()
    } catch (error: any) {
      console.error('Failed to create tunnel:', error)
      const detail = error.response?.data?.detail
      let errorMsg = 'Failed to create tunnel'
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

  const getSpecForType = (core: string, type: string): Record<string, any> => {
    const baseSpec: Record<string, any> = {}

    if (core === 'rathole') {
      return { ...baseSpec, remote_addr: '', token: '', local_addr: '127.0.0.1:8080' }
    }

    switch (type) {
      case 'grpc':
        return { ...baseSpec, service_name: 'GrpcService', uuid: generateUUID() }
      case 'udp':
        return { ...baseSpec, uuid: generateUUID(), header_type: 'none' }
      default:
        return baseSpec
    }
  }

  const handleCoreChange = (core: string) => {
    let newType = formData.type
    const updated = { ...formData, core }
    if (core === 'chisel') {
      newType = (formData.type === 'tcp' || formData.type === 'udp' || formData.type === 'tcp+udp' || formData.type === 'socks5') ? formData.type : 'tcp'
      if (!updated.chisel_control_port) {
        updated.chisel_control_port = generateRandomControlPort()
      }
    } else if (core === 'rathole') {
      newType = (formData.type === 'tcp' || formData.type === 'udp' || formData.type === 'tcp+udp') ? formData.type : 'tcp'
      if (!updated.rathole_remote_addr) {
        updated.rathole_remote_addr = generateRandomControlPort()
      }
    } else if (core === 'frp') {
      newType = (formData.type === 'tcp' || formData.type === 'udp' || formData.type === 'tcp+udp') ? formData.type : 'tcp'
      if (!updated.frp_bind_port || updated.frp_bind_port === '7000') {
        updated.frp_bind_port = generateRandomControlPort()
      }
    } else if (core === 'backhaul') {
      newType = (formData.type === 'tcp' || formData.type === 'udp' || formData.type === 'tcp+udp') ? formData.type : 'tcp'
      if (!backhaulState.control_port || backhaulState.control_port === '3080') {
        setBackhaulState(prev => ({ ...prev, control_port: generateRandomControlPort() }))
      }
    } else if (core === 'gost') {
      newType = (formData.type === 'tcp' || formData.type === 'udp' || formData.type === 'grpc' || formData.type === 'tcpmux') ? formData.type : 'tcp'
    } else {
      newType = 'tcp'
    }
    updated.type = newType
    setFormData(updated)
  }

  const generateUUID = () => {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0
      const v = c === 'x' ? r : (r & 0x3) | 0x8
      return v.toString(16)
    })
  }

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-xs flex items-center justify-center z-[100] p-3.5 sm:p-4">
      <div className="bg-white dark:bg-gray-800 rounded-2xl w-full max-w-2xl max-h-[90dvh] shadow-2xl border border-gray-200/80 dark:border-gray-700/80 flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <form onSubmit={handleSubmit} className="flex flex-col h-full max-h-[90dvh] overflow-hidden">
          {/* Sticky Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 dark:border-gray-700/70 bg-gray-50/70 dark:bg-gray-800/90 shrink-0">
            <div className="flex items-center gap-2.5">
              <span className="p-1.5 rounded-lg bg-blue-100 dark:bg-blue-900/50 text-blue-600 dark:text-blue-400">
                <Plus size={18} />
              </span>
              <h2 className="text-lg sm:text-xl font-bold text-gray-900 dark:text-white">
                {t.tunnels.createTunnel}
              </h2>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors cursor-pointer"
              title="Close"
            >
              <X size={18} />
            </button>
          </div>

          {/* Scrollable Form Body */}
          <div className="flex-1 overflow-y-auto custom-scrollbar p-4 sm:p-6 space-y-4 min-h-0">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
            <div>
              <label className="block text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                Name
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                className="w-full px-3.5 py-2.5 border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700/80 text-gray-900 dark:text-white text-sm font-medium focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-xs"
                placeholder="e.g. My-Backhaul-Tunnel"
                required
              />
            </div>
            <div>
              <div className="flex items-center justify-between mb-1.5 h-5">
                <label className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                  <Tag size={14} className="text-blue-500" />
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
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
            <div>
              <div className="flex items-center justify-between mb-1.5 h-5">
                <label className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300">
                  {t.tunnels.iranNode}
                </label>
              </div>
              <CustomSelect
                value={formData.iran_node_id || formData.node_id || ''}
                onChange={(val) => setFormData({ ...formData, iran_node_id: val, node_id: val })}
                options={[
                  { value: '', label: t.tunnels.selectIranNode || 'Select an Iran node' },
                  ...nodes.map((node) => ({
                    value: node.id,
                    label: node.name,
                    badge: node.ip_address,
                    icon: <Server size={14} className="text-blue-500" />
                  }))
                ]}
                placeholder={t.tunnels.selectIranNode || 'Select an Iran node'}
                required={formData.core === 'rathole' || formData.core === 'backhaul' || formData.core === 'frp' || formData.core === 'chisel'}
              />
            </div>
            <div>
              <div className="flex items-center justify-between mb-1.5 h-5">
                <label className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300">
                  {t.tunnels.foreignServer}
                </label>
              </div>
              <CustomSelect
                value={formData.foreign_node_id || ''}
                onChange={(val) => setFormData({ ...formData, foreign_node_id: val })}
                options={[
                  { value: '', label: t.tunnels.selectForeignServer || 'Select a Foreign server' },
                  ...servers.map((server) => ({
                    value: server.id,
                    label: server.name,
                    badge: server.ip_address,
                    icon: <Globe size={14} className="text-emerald-500" />
                  }))
                ]}
                placeholder={t.tunnels.selectForeignServer || 'Select a Foreign server'}
                required={formData.core === 'rathole' || formData.core === 'backhaul' || formData.core === 'frp' || formData.core === 'chisel'}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
            <div>
              <div className="flex items-center justify-between mb-1.5 h-5">
                <label className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300">
                  {t.tunnels.core}
                </label>
              </div>
              <CustomSelect
                value={formData.core}
                onChange={(val) => handleCoreChange(val)}
                options={[
                  { value: 'gost', label: 'GOST', icon: <Zap size={14} className="text-indigo-500" /> },
                  { value: 'rathole', label: 'Rathole', icon: <Rocket size={14} className="text-amber-500" /> },
                  { value: 'backhaul', label: 'Backhaul', icon: <RadioTower size={14} className="text-blue-500" /> },
                  { value: 'chisel', label: 'Chisel', icon: <Network size={14} className="text-cyan-500" /> },
                  { value: 'frp', label: 'FRP', icon: <Cpu size={14} className="text-purple-500" /> },
                ]}
              />
            </div>
            <div>
              <div className="flex items-center justify-between mb-1.5 h-5">
                <label className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-300">
                  {t.tunnels.type}
                </label>
              </div>
              <CustomSelect
                value={formData.type}
                onChange={(val) => {
                  const value = val as string
                  setFormData({ ...formData, type: value })
                  if (formData.core === 'backhaul') {
                    if (value === 'udp' || value === 'tcp+udp') {
                      setBackhaulState((prev) => ({ ...prev, accept_udp: true }))
                    } else if (value === 'tcp') {
                      setBackhaulState((prev) => ({ ...prev, accept_udp: false }))
                    }
                  }
                }}
                options={
                  formData.core === 'chisel'
                    ? [
                        { value: 'tcp', label: 'TCP (Standard)' },
                        { value: 'udp', label: 'UDP (Gaming / Anti-Lag)' },
                        { value: 'tcp+udp', label: 'TCP + UDP (Dual Forward)' },
                        { value: 'socks5', label: 'SOCKS5 (Dynamic Proxy)' },
                      ]
                    : formData.core === 'rathole'
                    ? [
                        { value: 'tcp', label: 'TCP' },
                        { value: 'udp', label: 'UDP (Gaming)' },
                        { value: 'tcp+udp', label: 'TCP + UDP' },
                      ]
                    : formData.core === 'frp'
                    ? [
                        { value: 'tcp', label: 'TCP' },
                        { value: 'udp', label: 'UDP' },
                      ]
                    : formData.core === 'backhaul'
                    ? [
                        { value: 'tcp', label: 'TCP (Standard)' },
                        { value: 'udp', label: 'UDP (Gaming / Anti-Lag)' },
                        { value: 'tcp+udp', label: 'TCP + UDP (Dual Forward)' },
                      ]
                    : [
                        { value: 'tcp', label: 'TCP' },
                        { value: 'udp', label: 'UDP' },
                        { value: 'grpc', label: 'gRPC' },
                        { value: 'tcpmux', label: 'TCP Mux' },
                      ]
                }
              />
            </div>
          </div>

                    {/* Dynamic Core Configuration Sections */}
          {formData.core === 'gost' && (
            <GostSection
              formData={formData}
              onChange={(updates) => setFormData((prev) => ({ ...prev, ...updates }))}
              showToast={showToast}
            />
          )}

          {formData.core === 'backhaul' && (
            <BackhaulSection
              state={backhaulState}
              advancedState={backhaulAdvanced}
              onChange={(partial) => {
                setBackhaulState((prev) => ({ ...prev, ...partial }))
                if (partial.transport) {
                  setFormData((prev) => ({ ...prev, transport_type: partial.transport as string }))
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
                      },
                    }))
                  }
                }
              }}
              onAdvancedChange={setBackhaulAdvanced}
              onOpenAdvanced={() => setShowBackhaulAdvanced(true)}
              acceptUdpVisible={
                backhaulState.transport === 'tcp' ||
                backhaulState.transport === 'tcpmux' ||
                backhaulState.transport === 'udp'
              }
              showToast={showToast}
            />
          )}

          {formData.core === 'rathole' && (
            <RatholeSection
              formData={formData}
              onChange={(updates) => setFormData((prev) => ({ ...prev, ...updates }))}
              showToast={showToast}
            />
          )}

          {formData.core === 'chisel' && (
            <ChiselSection
              formData={formData}
              onChange={(updates) => setFormData((prev) => ({ ...prev, ...updates }))}
              showToast={showToast}
            />
          )}

          {formData.core === 'frp' && (
            <FrpSection
              formData={formData}
              onChange={(updates) => setFormData((prev) => ({ ...prev, ...updates }))}
              showToast={showToast}
            />
          )}

          {/* v4 to v6 tunnel toggle card - only for Rathole, Backhaul, Chisel, FRP (not GOST) */}
          {formData.core !== 'gost' && (
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
                {t.tunnels.createTunnel}
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


export default AddTunnelModal

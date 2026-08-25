/**
 * Safely copy text to clipboard across all browser contexts,
 * including non-secure contexts (HTTP with raw IP addresses).
 */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (!text) return false

  // 1. Try modern navigator.clipboard if available and in secure context
  if (typeof navigator !== 'undefined' && navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch (err) {
      console.warn('navigator.clipboard.writeText failed, falling back to execCommand:', err)
    }
  }

  // 2. Fallback to execCommand('copy') using a temporary textarea
  try {
    const textArea = document.createElement('textarea')
    textArea.value = text
    
    // Prevent scrolling and keep invisible
    textArea.style.position = 'fixed'
    textArea.style.top = '0'
    textArea.style.left = '0'
    textArea.style.width = '2em'
    textArea.style.height = '2em'
    textArea.style.padding = '0'
    textArea.style.border = 'none'
    textArea.style.outline = 'none'
    textArea.style.boxShadow = 'none'
    textArea.style.background = 'transparent'
    textArea.style.opacity = '0'
    textArea.setAttribute('readonly', '')
    
    document.body.appendChild(textArea)
    textArea.focus()
    textArea.select()
    textArea.setSelectionRange(0, text.length)
    
    const successful = document.execCommand('copy')
    document.body.removeChild(textArea)
    
    if (successful) {
      return true
    }
  } catch (err) {
    console.error('execCommand copy failed:', err)
  }

  return false
}

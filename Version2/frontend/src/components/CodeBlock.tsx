import { useState } from 'react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'

interface Props {
  language: string
  value: string
}

/** A syntax-highlighted code block with a language label and a copy button. */
export function CodeBlock({ language, value }: Props) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable (e.g. insecure context) — ignore */
    }
  }

  return (
    <div className="code-block" data-testid="code-block">
      <div className="code-block-head">
        <span className="code-lang">{language || 'text'}</span>
        <button type="button" className="copy-btn" onClick={copy} aria-label="Copy code">
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <SyntaxHighlighter
        language={language || 'text'}
        style={oneDark}
        PreTag="div"
        customStyle={{ margin: 0, background: 'transparent', padding: '12px 14px', fontSize: 13 }}
        codeTagProps={{ style: { fontFamily: 'var(--mono)' } }}
      >
        {value}
      </SyntaxHighlighter>
    </div>
  )
}

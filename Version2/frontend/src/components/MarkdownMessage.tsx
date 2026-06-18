import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CodeBlock } from './CodeBlock'

const components: Components = {
  code(props) {
    const { className, children } = props
    const match = /language-(\w+)/.exec(className || '')
    const value = String(children ?? '').replace(/\n$/, '')
    // Treat fenced (language tag) or multi-line code as a block; the rest inline.
    const isBlock = Boolean(match) || value.includes('\n')
    if (isBlock) {
      return <CodeBlock language={match?.[1] ?? 'text'} value={value} />
    }
    return <code className="inline-code">{children}</code>
  },
  pre(props) {
    // The code renderer already emits a fully styled block, so drop the default
    // <pre> wrapper to avoid redundant nesting.
    return <>{props.children}</>
  },
  a(props) {
    return (
      <a href={props.href} target="_blank" rel="noreferrer noopener">
        {props.children}
      </a>
    )
  },
}

interface Props {
  text: string
}

/** Renders assistant markdown with GFM, code highlighting, and copy buttons. */
export function MarkdownMessage({ text }: Props) {
  return (
    <div className="markdown">
      <Markdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </Markdown>
    </div>
  )
}

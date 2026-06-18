import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * Catches render-time exceptions anywhere below it so a single bad render can't
 * blank the whole app. Without this, an uncaught error unmounts the entire tree
 * and the user just sees a black screen with no clue what happened.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  handleReload = () => {
    this.setState({ error: null })
    window.location.reload()
  }

  render() {
    if (this.state.error) {
      return (
        <div className="app unauth">
          <div className="unauth-box">
            <div className="unauth-title">⚠ Something broke</div>
            <p>The UI hit an unexpected error and stopped rendering.</p>
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, opacity: 0.7, margin: '12px 0' }}>
              {this.state.error.message}
            </pre>
            <button className="btn-edit-save" onClick={this.handleReload}>
              Reload
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

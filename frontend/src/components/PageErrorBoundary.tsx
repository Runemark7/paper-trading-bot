import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = {
  label?: string;
  children: ReactNode;
};

type State = { error: Error | null };

/** One bad champion / discovery row must not white-screen the shell. */
export default class PageErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(this.props.label ?? "page", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const label = this.props.label ?? "This page";
    return (
      <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 p-4 text-sm text-rose-100 space-y-3 min-w-0">
        <p className="font-medium min-w-0 break-words">
          {label} crashed — the rest of the app is still up.
        </p>
        <p className="text-rose-100/80 min-w-0 break-words [overflow-wrap:anywhere]">
          {error.message || String(error)}
        </p>
        <button
          type="button"
          className="min-h-11 px-3 py-2 rounded bg-white/10 hover:bg-white/20 text-white"
          onClick={() => this.setState({ error: null })}
        >
          Try again
        </button>
      </div>
    );
  }
}

import { Component } from "react";

/**
 * The last-chance net: a render crash anywhere below shows the shop's own
 * error page instead of a blank white screen. Class component on purpose —
 * React still has no hook for catching a child's render error.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Surfaced for anyone looking at the console — the shop keeps no
    // telemetry service, so this line is the crash report.
    console.error("The shop hit an error it couldn't bake through:", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-5 px-4 text-center">
        <img src="/static/site/logo.png" alt="" className="h-16 w-16 object-contain" />
        <h1 className="font-display text-3xl font-semibold">
          Something spilled in the kitchen
        </h1>
        <p className="max-w-md text-sm opacity-70">
          The page hit an unexpected error — your basket and account are safe.
          Reload to pick up where you left off.
        </p>
        <div className="flex flex-wrap justify-center gap-3">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => window.location.reload()}
          >
            Reload the page
          </button>
          <a href="/" className="btn btn-ghost border border-base-300">
            Back to the shop
          </a>
        </div>
      </div>
    );
  }
}

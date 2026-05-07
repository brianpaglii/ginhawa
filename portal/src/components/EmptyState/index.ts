// Barrel re-export so consumers can keep
//   import { EmptyState, SessionsEmptyIcon } from "../components/EmptyState"
// while the underlying files split components from icon constants
// (icons can't live next to the component without tripping the
// react-refresh "only-export-components" rule).

export { EmptyState } from "./EmptyState";
export { AuditEmptyIcon, CitizensEmptyIcon, SessionsEmptyIcon } from "./icons";

/**
 * @garh/ui — shared UI primitives.
 *
 * Consumed as TypeScript SOURCE, same as @garh/model: `main`/`types` point at
 * this file and Vite compiles it with the app. There is no build step, so the
 * workspace tsconfig must keep `"moduleResolution": "bundler"` (relative
 * imports here are extensionless).
 *
 * Setup an app must do once:
 *   1. `import '@garh/ui/tokens.css'` before your own stylesheet.
 *   2. `presets: [require('@garh/ui/tailwind-preset')]` in tailwind.config.cjs.
 *   3. Add `'../../packages/ui/src/**\/*.{ts,tsx}'` to Tailwind `content`.
 *   4. Wrap the root in `<ToastProvider>`.
 *   5. Call `initTheme()` once before React mounts.
 *
 * The only cross-package dependency is @garh/model, used by LengthInput to
 * parse and format millimetres. This package never imports from apps/*.
 */

export { cn } from './cn';
export type { ClassValue } from './cn';

export { Icon, Spinner, ICON_NAMES } from './icons';
export type { IconName, IconProps } from './icons';

export {
  useIsomorphicLayoutEffect,
  focusableWithin,
  useFocusTrap,
  useOnEscape,
  useOnOutsidePointerDown,
  useBodyScrollLock,
  usePrefersReducedMotion,
  useControllableState,
} from './hooks';

export {
  applyTheme,
  initTheme,
  readStoredTheme,
  resolveTheme,
  storeTheme,
  THEME_STORAGE_KEY,
} from './theme';
export type { Theme } from './theme';

export { Button, LinkButton, IconButton } from './Button';
export type { ButtonProps, LinkButtonProps, IconButtonProps, ButtonSize, ButtonVariant } from './Button';

export { Field, CONTROL_CLASS, CONTROL_INVALID_CLASS } from './Field';
export type { FieldProps, FieldRenderArgs } from './Field';

export {
  Input,
  Textarea,
  LengthInput,
  PhoneInput,
  OtpInput,
  normaliseIndianMobile,
  formatIndianMobile,
  isPlausibleIndianMobile,
} from './Input';
export type {
  InputProps,
  TextareaProps,
  LengthInputProps,
  PhoneInputProps,
  OtpInputProps,
} from './Input';

export { Select, SelectField } from './Select';
export type { SelectOption, SelectProps, SelectFieldProps } from './Select';

export { Dialog, ConfirmDialog } from './Dialog';
export type { DialogProps, DialogSize, ConfirmDialogProps } from './Dialog';

export { ToastProvider, Toaster, useToast } from './Toast';
export type { ToastAction, ToastInput, ToastSeverity, ToastProviderProps } from './Toast';

export { Tooltip, ShortcutHint } from './Tooltip';
export type { TooltipProps, TooltipPlacement } from './Tooltip';

export { Chip, ComplianceChip, AssumptionChip, SEVERITY_ICON } from './Chip';
export type {
  ChipProps,
  ChipSeverity,
  ChipSize,
  ComplianceChipProps,
  ComplianceStatus,
  AssumptionChipProps,
} from './Chip';

export { Badge, CountBadge } from './Badge';
export type { BadgeProps, BadgeTone } from './Badge';

export { Card, CardLink, CardHeader, CardBody, CardFooter, PanelSection, DataRow } from './Card';
export type { CardProps, CardLinkProps } from './Card';

export {
  Skeleton,
  SkeletonRegion,
  SkeletonText,
  SkeletonProjectCard,
  SkeletonForm,
  SkeletonCanvas,
} from './Skeleton';
export type { SkeletonProps } from './Skeleton';

export { Tabs, TabPanel, TabLinks } from './Tabs';
export type { TabItem, TabsProps, TabsVariant, TabLinkItem, TabLinksProps } from './Tabs';

export { EmptyState, PhasePlaceholder, demoProjectAction } from './EmptyState';
export type {
  EmptyStateProps,
  EmptyStateAction,
  EmptyStateDemo,
  DemoNotApplicable,
} from './EmptyState';

export { ProgressRing, ProgressBar, scoreBand } from './ProgressRing';
export type { ProgressRingProps, ProgressBarProps, ScoreBand } from './ProgressRing';

export { whatsappShareUrl, buildShareMessage, copyToClipboard } from './share';
export type { ShareMessageInput } from './share';

import type { ButtonHTMLAttributes } from "react";

type ButtonSize = "sm" | "md" | "lg";
type ButtonVariant = "default" | "outline" | "destructive" | "ghost";

const sizeClasses: Record<ButtonSize, string> = {
  sm: "h-7 px-2.5 text-xs",
  md: "h-9 px-4 text-sm",
  lg: "h-11 px-6 text-base",
};

const variantClasses: Record<ButtonVariant, string> = {
  default: "bg-brand text-brand-foreground hover:opacity-90",
  outline: "border border-border text-foreground hover:bg-muted/20",
  destructive: "bg-status-critical text-brand-foreground hover:opacity-90",
  ghost: "text-foreground hover:bg-muted/20",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  size?: ButtonSize;
  variant?: ButtonVariant;
}

export function Button({
  size = "md",
  variant = "default",
  className = "",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${sizeClasses[size]} ${variantClasses[variant]} ${className}`.trim()}
      {...rest}
    >
      {children}
    </button>
  );
}

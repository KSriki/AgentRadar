import { cn } from "@/lib/utils";

type Variant = "default" | "success" | "destructive" | "muted";

export function Badge({
  variant = "default",
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { variant?: Variant }) {
  const styles: Record<Variant, string> = {
    default: "bg-primary text-primary-foreground",
    success: "bg-success text-white",
    destructive: "bg-destructive text-white",
    muted: "bg-muted text-muted-foreground",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium",
        styles[variant],
        className
      )}
      {...props}
    />
  );
}
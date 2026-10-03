import { z } from "zod";

// Form validation remains lazy with Settings; runtime menus use quickActions.
export const quickActionMenuSchema = z
  .object({
    enabled: z.boolean(),
    order: z.array(z.string().max(64)).max(32),
    hidden: z.array(z.string().max(64)).max(32),
  })
  .strict();

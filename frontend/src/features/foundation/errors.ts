// describeApiError moved to the shared @/lib/errorMessages module so every
// feature (not just foundation) maps backend errors to Russian consistently.
// Re-exported here to keep existing imports working.
export { describeApiError } from "@/lib/errorMessages";

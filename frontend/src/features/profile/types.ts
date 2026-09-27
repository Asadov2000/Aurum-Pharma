export interface ProfileAssignment {
  role_id: string;
  role_name: string;
  scope: "tenant" | "branch";
  branch: { id: string; name: string } | null;
}

export interface PersonalProfile {
  id: string;
  full_name: string;
  email: string;
  status: string;
  tenant_id: string | null;
  is_tenant_owner: boolean;
  platform_role: "developer" | "administrator" | null;
  access_assignments: ProfileAssignment[];
}

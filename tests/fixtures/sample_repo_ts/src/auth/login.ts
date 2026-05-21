/** Auth module — fixture for TypeScript parser + indexer tests. */

const PRIVATE_TOKEN = "abc";

export function authenticate(email: string, password: string): boolean {
  return password === PRIVATE_TOKEN && email.includes("@");
}

export async function fetchUser(userId: number): Promise<unknown> {
  return { id: userId };
}

export const computeRole = (claims: Record<string, string>): string => {
  return claims["role"] ?? "guest";
};

export class LoginForm {
  constructor(public email: string) {}

  validate(): boolean {
    return this.email.includes("@");
  }

  async submit(): Promise<boolean> {
    return authenticate(this.email, PRIVATE_TOKEN);
  }
}

export interface Session {
  email: string;
  expiresAt: number;
}

class InternalCache {
  store(key: string, value: unknown): void {}
}

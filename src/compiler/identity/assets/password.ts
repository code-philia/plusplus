// Compiler-owned password encoding. No plaintext passwords or reusable default credentials.
import { randomBytes, scrypt, scryptSync, timingSafeEqual } from "node:crypto";

const options = { N: 16384, r: 8, p: 1, maxmem: 64 * 1024 * 1024 };
const keyLength = 64;

export function validPasswordInput(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && Buffer.byteLength(value, "utf8") <= 1024;
}

export function hashIdentityPassword(password: string): string {
  if (!validPasswordInput(password)) throw new Error("Password must be nonempty and at most 1024 UTF-8 bytes");
  const salt = randomBytes(16);
  const hash = scryptSync(password, salt, keyLength, options);
  return ["scrypt", "16384", "8", "1", salt.toString("base64"), hash.toString("base64")].join("$");
}

export async function verifyIdentityPassword(password: string, encoded: string | null): Promise<boolean> {
  const parts = encoded?.split("$") ?? [];
  const supported = parts.length === 6 && parts.slice(0, 4).join("$") === "scrypt$16384$8$1";
  const salt = supported ? Buffer.from(parts[4], "base64") : Buffer.alloc(16);
  const expected = supported ? Buffer.from(parts[5], "base64") : Buffer.alloc(keyLength);
  const wellFormed = supported && salt.length === 16 && expected.length === keyLength;
  // Unknown users and disabled accounts still perform the same expensive derivation.
  const actual = await new Promise<Buffer>((resolve, reject) => {
    scrypt(password, wellFormed ? salt : Buffer.alloc(16), keyLength, options,
      (error, value) => error ? reject(error) : resolve(value));
  });
  return wellFormed && timingSafeEqual(actual, expected);
}

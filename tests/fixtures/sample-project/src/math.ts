export function add(a: number, b: number): number {
  return a + b;
}

export function multiply(a: number, b: number): number {
  return a * b;
}

export class MathHelper {
  static square(n: number): number {
    return multiply(n, n);
  }
}

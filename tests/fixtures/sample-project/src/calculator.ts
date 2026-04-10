import { add, multiply } from "./math";
import { MathHelper } from "./math";

export class Calculator {
  add(a: number, b: number): number {
    return add(a, b);
  }

  square(n: number): number {
    return MathHelper.square(n);
  }
}

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library unmounts after each test only when Vitest's globals are on; they're off here.
afterEach(() => cleanup());

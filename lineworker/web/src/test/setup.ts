import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// vitest runs with globals:false, so testing-library's auto-cleanup
// never registers — do it explicitly.
afterEach(cleanup);

import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import App from "./App";

test("shell renders the LINEWORKER brand mark", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "LINEWORKER" })).toBeInTheDocument();
});

test("shell demonstrates the dense card frame", () => {
  render(<App />);
  expect(screen.getByRole("region", { name: /sample claim card/i })).toBeInTheDocument();
});

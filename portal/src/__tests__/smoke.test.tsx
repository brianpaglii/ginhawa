import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

function Hello() {
  return <h1>GINHAWA Portal</h1>;
}

describe("smoke", () => {
  it("renders a trivial component", () => {
    render(<Hello />);
    expect(
      screen.getByRole("heading", { name: "GINHAWA Portal" }),
    ).toBeInTheDocument();
  });
});

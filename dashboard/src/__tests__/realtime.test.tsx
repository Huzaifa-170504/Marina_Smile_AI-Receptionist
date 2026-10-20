import { render } from "@testing-library/react";
import { act } from "react";
import { describe, expect, it, vi } from "vitest";

import { useClinicRealtime } from "@/hooks/use-clinic-realtime";

const removeChannel = vi.fn();
const subscribe = vi.fn();
let changeHandler: (() => void) | null = null;
const channel = {
  on: vi.fn((_event: string, _filter: object, handler: () => void) => { changeHandler = handler; return channel; }),
  subscribe,
};

vi.mock("@/lib/supabase/browser", () => ({
  createClient: () => ({ channel: () => channel, removeChannel }),
}));

function Harness({ onChange }: { onChange: () => void }) {
  useClinicRealtime("clinic-1", ["appointments"], onChange);
  return null;
}

describe("clinic realtime", () => {
  it("invalidates on a scoped event and removes the channel on unmount", () => {
    const onChange = vi.fn();
    const view = render(<Harness onChange={onChange} />);
    expect(subscribe).toHaveBeenCalledOnce();
    act(() => changeHandler?.());
    expect(onChange).toHaveBeenCalledOnce();
    view.unmount();
    expect(removeChannel).toHaveBeenCalledWith(channel);
  });
});

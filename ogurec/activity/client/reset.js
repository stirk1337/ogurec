/** Discord Activity iframe has no `allow-modals`; `window.confirm` returns false and the click looks dead. */

export function isResetAck(raw, userId) {
  try {
    const state = JSON.parse(raw);
    return state?.type === "reset" && String(state.id) === String(userId);
  } catch {
    return false;
  }
}

export function resetPayload({id, channelId, instanceId, day}) {
  return {
    type: "reset",
    id,
    channelId,
    instanceId,
    day,
  };
}

export function createResetSession() {
  let phase = "idle";
  return {
    get phase() {
      return phase;
    },
    request() {
      if (phase === "wiping") return phase;
      phase = "confirm";
      return phase;
    },
    cancel() {
      if (phase === "wiping") return phase;
      phase = "idle";
      return phase;
    },
    beginWipe() {
      if (phase !== "confirm") return false;
      phase = "wiping";
      return true;
    },
  };
}

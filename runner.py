"""
Executes a compiled test script (the JSON produced by the block editor).

Script shape:
{
  "name": "my_test",
  "steps": [
    {"type": "find_button", "template": "save_icon.png", "search": "spiral_cw",
     "start_x": 640, "start_y": 360, "radius": 400, "threshold": 0.8},
    {"type": "click", "target": "last_found"},
    ...
  ]
}
"""

import traceback

import actions
import locators


class StepFailed(Exception):
    pass


def _resolve_target(step, context):
    """Return (x, y) for a step that can act on 'last_found' or explicit coords."""
    target = step.get("target", "coords")
    if target == "last_found":
        if context.get("last_found") is None:
            raise StepFailed("target is 'last_found' but nothing has been located yet")
        a,b = context["last_found"]
        return a + step.get("x",0), b + step.get("y",0)
    return step.get("x", 0), step.get("y", 0)


def run_step(step, context, log):
    """Execute a single step. Mutates `context` (e.g. last_found). Appends to `log`."""
    step_type = step["type"]

    if step_type == "find_button":
        (coords, score) = locators.find_button(
            template_path=step["template"],
            search=step.get("search", "spiral_cw"),
            start_x=step.get("start_x"),
            start_y=step.get("start_y"),
            radius=step.get("radius", 400),
            threshold=step.get("threshold", 0.8),
            bounds=context.get("bounds"),
        )
        if coords is None:
            raise StepFailed(f"find_button: '{step['template']}' not found (best score {score:.2f})")
        context["last_found"] = coords
        log.append(f"found button '{step['template']}' at {coords} (score {score:.2f})")

    elif step_type == "find_text":
        coords, ratio = locators.find_text(step["text"], bounds=context.get("bounds"))
        if coords is None:
            raise StepFailed(f"find_text: '{step['text']}' not found (best match {ratio:.2f})")
        context["last_found"] = coords
        log.append(f"found text '{step['text']}' at {coords} (match {ratio:.2f})")

    elif step_type == "click":
        x, y = _resolve_target(step, context)
        actions.click(x, y, button=step.get("button", "left"))
        log.append(f"clicked at {(x, y)}")

    elif step_type == "move":
        actions.move(step["x"], step["y"])
        log.append(f"moved to {(step['x'], step['y'])}")

    elif step_type == "type_text":
        if step.get("target") == "last_found":
            x, y = _resolve_target(step, context)
            actions.click(x, y)
        actions.type_text(step["text"])
        log.append(f"typed: {step['text']!r}")

    elif step_type == "drag":
            click_first = step.get("click_first", False)
            actions.drag(step["x1"], step["y1"], step["x2"], step["y2"], click_first=click_first)
            prefix = "click + " if click_first else ""
            log.append(f"{prefix}dragged {(step['x1'], step['y1'])} -> {(step['x2'], step['y2'])}")

    elif step_type == "press_key":
        count = step.get("count", 1)
        actions.press_key(step["key"], count=count)
        log.append(f"pressed key: {step['key']} x{count}")

    elif step_type == "wait":
        actions.wait(step["ms"])
        log.append(f"waited {step['ms']}ms")

    elif step_type == "repeat":
        # handled by run_script (needs to know about the following step) — no-op here
        pass

    else:
        raise StepFailed(f"unknown step type: {step_type}")


def run_script(script, on_progress=None):
    """
    Executes `script["steps"]` in order. 'repeat' applies to the single step
    that follows it (flat semantics, matching the block editor's Repeat block).

    Script-level settings:
      bounds               optional {"left","top","width","height"} — restricts
                           where locator steps search on screen
      continue_on_failure  if true, a failing step is logged and the run
                           carries on instead of aborting at that point

    on_progress(index, total, log_line) is called after each executed step,
    useful for streaming status back to a polling client.
    """
    context = {"last_found": None, "bounds": script.get("bounds")}
    log = []
    steps = script["steps"]
    continue_on_failure = bool(script.get("continue_on_failure"))
    failed_steps = []
    i = 0
    total = len(steps)

    if context["bounds"]:
        b = context["bounds"]
        log.append(f"search bounds: left={b['left']} top={b['top']} "
                   f"width={b['width']} height={b['height']}")

    def attempt(step, index):
        """
        Run one step. Returns True to keep going, False to abort the run.
        Records the failure either way so the final status reflects it.
        """
        try:
            run_step(step, context, log)
            return True
        except StepFailed as e:
            log.append(f"FAILED at step {index}: {e}")
        except Exception:
            log.append(f"ERROR at step {index}:\n{traceback.format_exc()}")
        failed_steps.append(index)
        if continue_on_failure:
            log.append("continuing anyway (continue-on-failure is on)")
            return True
        return False

    while i < total:
        step = steps[i]

        if step["type"] == "repeat":
            count = step.get("count", 1)
            if i + 1 >= total:
                log.append(f"FAILED at step {i}: repeat block has no following step to repeat")
                failed_steps.append(i)
                if not continue_on_failure:
                    break
                i += 1
                continue

            target_step = steps[i + 1]
            aborted = False
            for n in range(count):
                log.append(f"-- repeat {n + 1}/{count} --")
                if not attempt(target_step, i + 1):
                    aborted = True
                    break
                if on_progress:
                    on_progress(i + 1, total, log[-1])
            if aborted:
                break
            i += 2
            continue

        if not attempt(step, i):
            break
        if on_progress:
            on_progress(i, total, log[-1])
        i += 1

    if not failed_steps:
        return {"status": "passed", "log": log}

    if continue_on_failure:
        log.append(f"run finished with {len(failed_steps)} failed step(s): {failed_steps}")

    return {"status": "failed", "log": log, "failed_step": failed_steps[0],
            "failed_steps": failed_steps}

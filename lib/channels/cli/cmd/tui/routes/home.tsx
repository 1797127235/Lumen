import { Prompt, type PromptRef } from "@tui/component/prompt"
import { createSignal } from "solid-js"
import { Logo } from "../component/logo"
import { Toast } from "../ui/toast"
import { usePromptRef } from "../context/prompt"

const placeholder = {
  normal: ["What can Lumen help you with?", "Explain this codebase", "Fix a bug in my code"],
  shell: [],
}

export function Home() {
  const promptRef = usePromptRef()
  const [_ref, setRef] = createSignal<PromptRef | undefined>()

  const bind = (r: PromptRef | undefined) => {
    setRef(r)
    promptRef.set(r)
  }

  return (
    <>
      <box flexGrow={1} alignItems="center" paddingLeft={2} paddingRight={2}>
        <box flexGrow={1} minHeight={0} />
        <box height={4} minHeight={0} flexShrink={1} />
        <box flexShrink={0}>
          <Logo />
        </box>
        <box height={1} minHeight={0} flexShrink={1} />
        <box width="100%" maxWidth={75} zIndex={1000} paddingTop={1} flexShrink={0}>
          <Prompt ref={bind} placeholders={placeholder} />
        </box>
        <box flexGrow={1} minHeight={0} />
        <Toast />
      </box>
    </>
  )
}

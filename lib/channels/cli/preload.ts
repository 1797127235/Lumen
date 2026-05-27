import { plugin } from "bun"
import { SolidPlugin } from "bun-plugin-solid"

plugin(SolidPlugin({ generate: "universal", moduleName: "@opentui/solid" }))

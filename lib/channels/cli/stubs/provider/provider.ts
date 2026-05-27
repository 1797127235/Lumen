export const Provider = {
  parseModel(model: string): { providerID: string; modelID: string } {
    const slash = model.indexOf("/")
    if (slash === -1) return { providerID: model, modelID: "" }
    return {
      providerID: model.slice(0, slash),
      modelID: model.slice(slash + 1),
    }
  },
}

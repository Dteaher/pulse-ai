export function modelLabel(model: string) {
  return model.startsWith('gpt://')
    ? model.slice(6).split('/').slice(1).join('/')
    : (model.split('/').pop() ?? model).replace(/:free$/, '');
}

export function providerLabel(provider: string) {
  return ({polza: 'Polza AI', multiai: 'MultiAI', vertex_gemini: 'Vertex Gemini'} as Record<string,string>)[provider] ?? provider;
}
export function friendlyModelLabel(model: string) {
  const label = modelLabel(model);
  return ({'gpt-6-luna': 'GPT-6 Luna', 'gpt-6-sol': 'GPT-6 Sol', 'gpt-6-astra': 'GPT-6 Astra', 'gpt-5.5': 'GPT-5.5'} as Record<string,string>)[label] ?? label;
}

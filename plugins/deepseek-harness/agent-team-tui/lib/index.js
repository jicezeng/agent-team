/**
 * Minimal native-terminal frontend for an Agent-Team-owned DeepSeek Harness
 * Session. It deliberately renders public model text and bounded tool status,
 * never private reasoning text. Agent-Team owns process lifetime and the
 * durable collaboration transition; this plugin owns only DSH interaction.
 */

import { createInterface } from 'node:readline'
import { installModelSelection } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { SessionId } from '@deepseek-ai/dsh-session'

export const name = 'agent-team-tui'
export const inject = ['cmdlineArgs', 'agents', 'sessions', 'agentDefaultModel']

function fail(message) {
  throw new Error(`agent-team-tui: ${message}`)
}

function parseArgs(argv) {
  const values = new Map()
  const positional = []
  const valueFlags = new Set([
    '--session-id',
    '--resume',
    '--provider',
    '--model',
    '--reasoning-effort',
  ])
  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index]
    if (item === '--help' || item === '-h') return { help: true }
    if (valueFlags.has(item)) {
      const value = argv[index + 1]
      if (value === undefined || value === '' || value.startsWith('--')) {
        fail(`${item} requires a value`)
      }
      if (values.has(item)) fail(`${item} may be supplied only once`)
      values.set(item, value)
      index += 1
      continue
    }
    if (item.startsWith('--')) fail(`unknown option ${item}`)
    positional.push(item)
  }
  const sessionId = values.get('--session-id')
  const resume = values.get('--resume')
  if ((sessionId === undefined) === (resume === undefined)) {
    fail('exactly one of --session-id or --resume is required')
  }
  const provider = values.get('--provider')
  const model = values.get('--model')
  if ((provider === undefined) !== (model === undefined)) {
    fail('--provider and --model must be supplied together')
  }
  const prompt = positional.join(' ')
  if (prompt.trim() === '') fail('a prompt is required')
  return {
    help: false,
    sessionId: sessionId ?? resume,
    resume: resume !== undefined,
    provider,
    model,
    reasoningEffort: values.get('--reasoning-effort'),
    prompt,
  }
}

function usage() {
  process.stdout.write(
    'Usage: dsh --profile agent-team (--session-id ID | --resume ID) '
      + '[--provider PROVIDER --model MODEL] [--reasoning-effort LEVEL] PROMPT\n',
  )
}

function userMessage(text) {
  return createUserMessage({
    content: [{ type: 'text', text }],
    source: { kind: 'user' },
  })
}

function installRenderer(ctx, sessionId) {
  let reasoningStep
  let textOpen = false
  let resolveInitialTurn
  const initialTurnReason = new Promise((resolve) => {
    resolveInitialTurn = resolve
  })
  const dispose = ctx.on('session/event', (session, event) => {
    if (String(session.id) !== sessionId) return
    if (event.type === 'assistant/chunk') {
      const chunk = event.data.chunk
      if (chunk.type === 'reasoning-delta') {
        const key = `${event.data.turn}:${event.data.step}`
        if (reasoningStep !== key) {
          if (textOpen) process.stdout.write('\n')
          process.stdout.write('[thinking]\n')
          reasoningStep = key
          textOpen = false
        }
      } else if (chunk.type === 'text-delta' && chunk.text !== '') {
        process.stdout.write(chunk.text)
        textOpen = true
      }
      return
    }
    if (event.type === 'tool/call') {
      if (textOpen) process.stdout.write('\n')
      process.stdout.write(`[tool] ${event.data.name}\n`)
      textOpen = false
      return
    }
    if (event.type === 'tool/result') {
      const state = event.data.error === undefined
        ? 'completed'
        : `failed: ${event.data.error.code}`
      process.stdout.write(`[tool] ${state}\n`)
      return
    }
    if (event.type === 'turn/end') {
      if (textOpen) process.stdout.write('\n')
      textOpen = false
      const reason = event.data.reason
      resolveInitialTurn?.(reason)
      resolveInitialTurn = undefined
      if (reason.kind !== 'completed') {
        process.stdout.write(`[turn] ${reason.kind}\n`)
      }
    }
  })
  return {
    dispose,
    initialTurnReason,
  }
}

function assertInitialTurnCompleted(reason) {
  if (reason?.kind === 'completed') return
  const detail = reason?.kind === 'error' && reason.error?.code
    ? `error (${reason.error.code})`
    : reason?.kind ?? 'missing terminal reason'
  fail(`initial agent turn did not complete: ${detail}`)
}

async function send(agent, sessions, text) {
  agent.followup(userMessage(text))
  await agent.whenIdle()
  await sessions.flush(agent.session)
}

async function start(ctx, options) {
  await ctx.get('loader')?.await()
  const agents = ctx.get('agents')
  const sessions = ctx.get('sessions')
  const defaultModel = ctx.get('agentDefaultModel')
  if (agents === undefined || sessions === undefined || defaultModel === undefined) return

  const selected = options.provider === undefined
    ? defaultModel.currentSelection()
    : { provider: options.provider, model: options.model }
  const current = {
    ...selected,
    ...(options.reasoningEffort === undefined
      ? {}
      : { reasoningEffort: options.reasoningEffort }),
  }
  const setup = (agentCtx) => {
    installModelSelection(agentCtx, { current, assembled: undefined })
  }
  const identity = SessionId(options.sessionId)
  const handle = options.resume
    ? await agents.resume({
        resumeSessionId: identity,
        agentOptions: { provider: current.provider, model: current.model },
        setup,
      })
    : await agents.create({
        sessionId: identity,
        meta: { cwd: process.cwd() },
        agentOptions: { provider: current.provider, model: current.model },
        setup,
      })
  const agent = handle.agent
  await agent.whenIdle()
  const renderer = installRenderer(ctx, options.sessionId)

  process.stdout.write(
    `DeepSeek Harness · Agent-Team interactive · ${options.sessionId}\n`,
  )
  await send(agent, sessions, options.prompt)
  assertInitialTurnCompleted(await renderer.initialTurnReason)

  const exit = ctx.get('appExit')
  const input = createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: Boolean(process.stdin.isTTY && process.stdout.isTTY),
    prompt: 'dsh> ',
  })
  let queued = Promise.resolve()
  input.on('line', (line) => {
    const text = line.trim()
    if (text === '') {
      input.prompt()
      return
    }
    if (text === '/exit' || text === '/quit') {
      input.close()
      return
    }
    queued = queued
      .then(() => send(agent, sessions, line))
      .catch((error) => {
        process.stderr.write(`dsh: ${error instanceof Error ? error.message : String(error)}\n`)
      })
      .finally(() => input.prompt())
  })
  input.on('close', () => {
    void queued.finally(async () => {
      renderer.dispose()
      await sessions.flush(agent.session)
      exit?.(0)
    })
  })
  input.prompt()
}

export function apply(ctx) {
  const argv = ctx.get('cmdlineArgs')?.get()
  if (argv === undefined) fail('the dsh launcher did not provide command-line arguments')
  const options = parseArgs(argv)
  if (options.help) {
    usage()
    ctx.get('appExit')?.(0)
    return
  }
  void start(ctx, options).catch((error) => {
    process.stderr.write(`dsh: ${error instanceof Error ? error.message : String(error)}\n`)
    ctx.get('appExit')?.(1)
  })
}

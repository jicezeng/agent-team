export const name = 'agent-team-origin'
export const inject = ['tools', 'subprocess', 'credentials']

const COLLECT_MAX_BYTES = 2 * 1024 * 1024
const GRACE_MS = 3000
const TIMEOUT_MS = 120000
const MAX_ARGS = 128
const MAX_ARG_BYTES = 8192
const PUBLIC_COMMANDS = new Set([
  '--version',
  'init',
  'start',
  'status',
  'watch',
  'diagnose',
  'transcript',
  'tail',
  'attach',
  'cancel',
  'recover',
  'unlock',
  'context',
  'handoff',
  'complete',
  'block',
  'wait-origin',
  'origin-context',
  'origin-handoff',
  'origin-complete',
  'origin-block',
  'origin-resume',
  'doctor',
])

const OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    exitCode: { oneOf: [{ type: 'integer' }, { type: 'null' }] },
    signal: { oneOf: [{ type: 'string' }, { type: 'null' }] },
    stdout: { type: 'string' },
    stderr: { type: 'string' },
  },
  required: ['exitCode', 'signal', 'stdout', 'stderr'],
}

const PARAMETERS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    args: {
      type: 'array',
      items: { type: 'string' },
      description: 'Agent-Team arguments beginning with a public command name.',
    },
  },
  required: ['args'],
}

function assertArgs(input) {
  if (
    typeof input !== 'object'
    || input === null
    || Array.isArray(input)
    || Reflect.ownKeys(input).length !== 1
    || !Object.hasOwn(input, 'args')
  ) {
    throw new Error('agent_team_cli requires exactly one args property')
  }
  const { args } = input
  if (
    !Array.isArray(args)
    || args.length === 0
    || args.length > MAX_ARGS
    || !PUBLIC_COMMANDS.has(args[0])
  ) {
    throw new Error('agent_team_cli requires one supported public Agent-Team command')
  }
  for (const value of args) {
    if (
      typeof value !== 'string'
      || value.includes('\0')
      || Buffer.byteLength(value, 'utf8') > MAX_ARG_BYTES
    ) {
      throw new Error('agent_team_cli received an invalid argument')
    }
  }
  return args
}

function renderResult(value) {
  const sections = []
  if (value.stdout.length > 0) sections.push(value.stdout.trimEnd())
  if (value.stderr.length > 0) sections.push(`stderr:\n${value.stderr.trimEnd()}`)
  sections.push(
    value.signal === null
      ? `exit code: ${String(value.exitCode)}`
      : `terminated by signal: ${value.signal}`,
  )
  return sections.join('\n')
}

export function apply(ctx) {
  let executablePromise

  const resolveExecutable = signal => {
    executablePromise ??= ctx.subprocess.resolveExecutable(
      process.env.AGENT_TEAM_CLI?.trim() || 'agent-team',
      undefined,
      signal,
    )
    return executablePromise
  }

  ctx.tools.register({
    name: 'agent_team_cli',
    description:
      'Run one public Agent-Team CLI command through the trusted DSH control plane. '
      + 'Pass argv after the executable as an array. Use this instead of Bash for '
      + 'Agent-Team init/start/wait/origin actions so DSH can forward its provider '
      + 'credential without exposing the value to the model or shell.',
    parameters: PARAMETERS_SCHEMA,
    output: {
      schema: OUTPUT_SCHEMA,
      render: (_args, value) => [{ type: 'text', text: renderResult(value) }],
    },
    timeoutMs: TIMEOUT_MS,
    async execute(input, exec) {
      const args = assertArgs(input)
      const executable = await resolveExecutable(exec.signal)
      const credential = await ctx.credentials.resolve('DEEPSEEK_API_KEY')
      const handle = ctx.subprocess.spawn({
        argv: [executable, ...args],
        cwd: exec.agent?.session.header.cwd ?? process.cwd(),
        stdio: {
          stdin: 'ignore',
          stdout: { maxBytes: COLLECT_MAX_BYTES },
          stderr: { maxBytes: COLLECT_MAX_BYTES },
        },
        graceMs: GRACE_MS,
        signal: exec.signal,
        env: credential === undefined
          ? undefined
          : { DEEPSEEK_API_KEY: credential.value },
      })
      const outcome = await handle.done
      const stdout = handle.collected.stdout?.readFrom(0)
      const stderr = handle.collected.stderr?.readFrom(0)
      if (
        stdout === undefined
        || stderr === undefined
        || stdout.lossy
        || stderr.lossy
      ) {
        throw new Error('agent_team_cli output exceeded its trusted collection bound')
      }
      return {
        exitCode: outcome.exitCode,
        signal: outcome.signal,
        stdout: stdout.text,
        stderr: stderr.text,
      }
    },
  })
}

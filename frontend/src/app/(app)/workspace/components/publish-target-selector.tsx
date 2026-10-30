import type { Account } from "@/lib/types"
import { channelName } from "@/lib/channels"

export function PublishTargetSelector({
  accounts,
  selected,
  onChange,
  disabled,
  label,
}: {
  accounts: Account[]
  selected: number[]
  onChange: (ids: number[]) => void
  disabled: boolean
  label?: string
}) {
  const byChannel: Record<string, Account[]> = {}
  for (const account of accounts) {
    if (!byChannel[account.channel]) byChannel[account.channel] = []
    byChannel[account.channel].push(account)
  }

  const validAccounts = accounts.filter((account) => account.valid && !disabled)
  const allSelected = validAccounts.length > 0 && validAccounts.every((account) => selected.includes(account.id))
  const someSelected = validAccounts.some((account) => selected.includes(account.id)) && !allSelected
  function toggleAll() {
    const validIds = validAccounts.map((account) => account.id)
    onChange(allSelected
      ? selected.filter((id) => !validIds.includes(id))
      : Array.from(new Set([...selected, ...validIds])))
  }

  function toggle(id: number) {
    onChange(selected.includes(id) ? selected.filter((value) => value !== id) : [...selected, id])
  }

  return (
    <div>
      {label && (
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs font-medium text-muted-foreground">{label}</span>
          <label className="flex items-center gap-1 cursor-pointer text-muted-foreground hover:text-foreground transition-colors">
            <input
              type="checkbox"
              checked={allSelected}
              ref={(element) => { if (element) element.indeterminate = someSelected }}
              disabled={validAccounts.length === 0 || disabled}
              onChange={toggleAll}
              className="accent-primary shrink-0"
            />
            <span className="text-xs">全选</span>
          </label>
        </div>
      )}
      <div className="grid grid-cols-2 gap-3">
        {Object.entries(byChannel).map(([channel, channelAccounts]) => {
          const validChannelAccounts = channelAccounts.filter((account) => account.valid && !disabled)
          const channelAllSelected = validChannelAccounts.length > 0 && validChannelAccounts.every((account) => selected.includes(account.id))
          const channelSomeSelected = validChannelAccounts.some((account) => selected.includes(account.id)) && !channelAllSelected
          const toggleChannel = () => {
            const validIds = validChannelAccounts.map((account) => account.id)
            onChange(channelAllSelected
              ? selected.filter((id) => !validIds.includes(id))
              : Array.from(new Set([...selected, ...validIds])))
          }
          return (
            <div key={channel}>
              <div className="flex items-center gap-1.5 mb-1.5">
                <input
                  type="checkbox"
                  checked={channelAllSelected}
                  ref={(element) => { if (element) element.indeterminate = channelSomeSelected }}
                  disabled={validChannelAccounts.length === 0 || disabled}
                  onChange={toggleChannel}
                  className="accent-primary shrink-0"
                />
                <p className="text-xs font-semibold text-muted-foreground">{channelName(channel)}</p>
              </div>
              <div className="flex flex-wrap gap-x-1.5 gap-y-0.5 pl-5">
                {channelAccounts.map((account) => (
                  <label
                    key={account.id}
                    className={["flex items-center gap-1 text-xs w-20 truncate", !account.valid || disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer"].join(" ")}
                    title={account.account_name}
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(account.id)}
                      disabled={!account.valid || disabled}
                      onChange={() => toggle(account.id)}
                      className="accent-primary shrink-0"
                    />
                    <span className="truncate">{account.account_name}</span>
                    {!account.valid && <span className="text-[10px] text-destructive shrink-0">(过期)</span>}
                  </label>
                ))}
              </div>
            </div>
          )
        })}
      </div>
      {accounts.length === 0 && <p className="mt-2 text-xs text-muted-foreground">暂无其他渠道账号</p>}
    </div>
  )
}

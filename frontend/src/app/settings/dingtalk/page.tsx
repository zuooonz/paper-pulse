"use client"
import { useState, useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import { useSession } from "next-auth/react"
import { useRouter } from "next/navigation"
import { fmtApiDate } from "@/lib/datetime"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Plus, Edit, Trash2 } from "lucide-react"

interface DingTalkUser {
  id: number
  dingtalk_userid: string
  internal_username: string
  role: string
  display_name: string | null
  created_at: string
  updated_at: string
}

export default function DingTalkManagementPage() {
  const { data: session } = useSession()
  const router = useRouter()
  const [error, setError] = useState("")
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<DingTalkUser | null>(null)
  const [formData, setFormData] = useState({
    dingtalk_userid: "",
    internal_username: "",
    role: "viewer",
  })

  const {
    data: users = [],
    isLoading: queryLoading,
    error: queryError,
    refetch: fetchUsers,
  } = useQuery<DingTalkUser[]>({
    queryKey: ["dingtalk-users"],
    enabled: Boolean(session?.user?.accessToken && session.user.role === "admin"),
    queryFn: async () => {
      const res = await fetch("/api/auth/dingtalk/users", {
        headers: { Authorization: `Bearer ${session?.user?.accessToken}` },
      })
      if (!res.ok) throw new Error("获取用户列表失败")
      return await res.json() as DingTalkUser[]
    },
  })
  const loading = queryLoading || !session?.user?.accessToken || session.user.role !== "admin"

  useEffect(() => {
    if (!session?.user?.accessToken) return
    if (session.user.role !== "admin") {
      router.push("/workspace")
      return
    }
  }, [session?.user?.accessToken, session?.user?.role, router])

  function openCreateDialog() {
    setEditingUser(null)
    setFormData({ dingtalk_userid: "", internal_username: "", role: "viewer" })
    setDialogOpen(true)
  }

  function openEditDialog(user: DingTalkUser) {
    setEditingUser(user)
    setFormData({
      dingtalk_userid: user.dingtalk_userid,
      internal_username: user.internal_username,
      role: user.role,
    })
    setDialogOpen(true)
  }

  async function handleSave() {
    setError("")
    try {
      const url = editingUser
        ? `/api/auth/dingtalk/users/${editingUser.id}`
        : "/api/auth/dingtalk/users"
      const method = editingUser ? "PUT" : "POST"

      const res = await fetch(url, {
        method,
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session?.user?.accessToken}`,
        },
        body: JSON.stringify(formData),
      })

      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || "保存失败")
      }

      setDialogOpen(false)
      void fetchUsers()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function handleDelete(userId: number) {
    if (!confirm("确定要删除这个用户映射吗？")) return
    try {
      const res = await fetch(`/api/auth/dingtalk/users/${userId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${session?.user?.accessToken}` },
      })
      if (!res.ok) {
        throw new Error("删除失败")
      }
      void fetchUsers()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <div className="container mx-auto p-6">
      <Card>
        <CardHeader>
          <CardTitle>钉钉用户管理</CardTitle>
          <CardDescription>
            管理钉钉用户与内部账号的映射关系。只有映射的用户才能使用钉钉登录。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-4">
            <Button onClick={openCreateDialog}>
              <Plus className="w-4 h-4 mr-2" />
              添加用户映射
            </Button>
          </div>

          {(error || queryError) && (
            <div className="mb-4 p-3 bg-destructive/10 text-destructive rounded">
              {error || (queryError instanceof Error ? queryError.message : "获取用户列表失败")}
            </div>
          )}

          {loading ? (
            <div className="text-center py-8 text-muted-foreground">加载中...</div>
          ) : users.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              暂无用户映射，点击&quot;添加用户映射&quot;开始配置
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>钉钉 UserID</TableHead>
                  <TableHead>内部用户名</TableHead>
                  <TableHead>角色</TableHead>
                  <TableHead>创建时间</TableHead>
                  <TableHead>更新时间</TableHead>
                  <TableHead className="w-[100px]">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users.map((user) => (
                  <TableRow key={user.id}>
                    <TableCell className="font-mono text-xs">
                      {user.dingtalk_userid}
                    </TableCell>
                    <TableCell>{user.internal_username}</TableCell>
                    <TableCell>{user.role}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {fmtApiDate(user.created_at, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {fmtApiDate(user.updated_at, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => openEditDialog(user)}
                        >
                          <Edit className="w-4 h-4" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => handleDelete(user.id)}
                        >
                          <Trash2 className="w-4 h-4 text-destructive" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {editingUser ? "编辑用户映射" : "添加用户映射"}
            </DialogTitle>
            <DialogDescription>
              {editingUser
                ? "更新钉钉用户与内部账号的映射关系"
                : "创建新的钉钉用户与内部账号映射"}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label>钉钉 UserID</Label>
              <Input
                value={formData.dingtalk_userid}
                onChange={(e) =>
                  setFormData({ ...formData, dingtalk_userid: e.target.value })
                }
                placeholder="从钉钉 API 获取的 userid"
                disabled={!!editingUser}
              />
              <p className="text-xs text-muted-foreground">
                钉钉用户的唯一标识，可通过钉钉 API 获取
              </p>
            </div>

            <div className="space-y-2">
              <Label>内部用户名</Label>
              <Select
                value={formData.internal_username}
                onValueChange={(value) =>
                  setFormData({ ...formData, internal_username: value || "" })
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择内部用户名" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">admin（管理员）</SelectItem>
                  <SelectItem value="reader">reader（只读用户）</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                映射到内部 USERS 字典中的用户名
              </p>
            </div>

            <div className="space-y-2">
              <Label>角色</Label>
              <Select
                value={formData.role}
                onValueChange={(value) =>
                  setFormData({ ...formData, role: value || "viewer" })
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择角色" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">admin（管理员）</SelectItem>
                  <SelectItem value="reviewer">reviewer（审核员）</SelectItem>
                  <SelectItem value="viewer">viewer（查看者）</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                用户在系统中的权限角色
              </p>
            </div>
          </div>

          {error && (
            <div className="p-3 bg-destructive/10 text-destructive rounded">
              {error}
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              取消
            </Button>
            <Button onClick={handleSave}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

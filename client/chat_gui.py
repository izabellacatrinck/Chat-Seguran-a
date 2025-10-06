import asyncio
import threading
import time
import tkinter as tk
from tkinter import Listbox, messagebox, scrolledtext, simpledialog

from chat_client_logic import ChatLogic


class CreateGroupDialog(simpledialog.Dialog):
    def __init__(self, parent, title, users):
        self.users = users
        self.selected_users = []
        super().__init__(parent, title)

    def body(self, master):
        tk.Label(master, text="Nome do Grupo:").grid(row=0, sticky=tk.W, padx=5, pady=5)
        self.group_name_entry = tk.Entry(master, width=30)
        self.group_name_entry.grid(row=0, column=1, padx=5, pady=5)

        tk.Label(master, text="Selecione os Membros:").grid(
            row=1, columnspan=2, sticky=tk.W, padx=5, pady=5
        )
        self.user_listbox = Listbox(master, selectmode=tk.MULTIPLE, height=8)
        for user in self.users:
            self.user_listbox.insert(tk.END, user)
        self.user_listbox.grid(row=2, columnspan=2, sticky=tk.EW, padx=5, pady=5)

        return self.group_name_entry

    def apply(self):
        group_name = self.group_name_entry.get().strip()
        selected_indices = self.user_listbox.curselection()
        selected_users = [self.user_listbox.get(i) for i in selected_indices]

        if group_name and selected_users:
            self.result = (group_name, selected_users)
        else:
            messagebox.showwarning(
                "Entrada Inválida",
                "Nome do grupo e pelo menos um membro são necessários.",
                parent=self,
            )
            self.result = None


class ChatGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Chat Seguro")
        self.root.geometry("700x500")
        self.logic = None
        self.current_chat = None
        self.loop = None

        self.login_frame = tk.Frame(root)
        tk.Label(self.login_frame, text="Seu ID:", font=("Helvetica", 12)).pack(
            padx=10, pady=5
        )
        self.id_entry = tk.Entry(self.login_frame, font=("Helvetica", 12), width=30)
        self.id_entry.pack(padx=10, pady=5)
        self.id_entry.bind("<Return>", lambda e: self.connect())
        tk.Button(
            self.login_frame,
            text="Entrar / Registrar",
            font=("Helvetica", 12),
            command=self.connect,
        ).pack(pady=10)
        self.login_frame.pack(expand=True)

        self.main_frame = tk.Frame(root)
        left_panel = tk.Frame(self.main_frame, width=200)
        tk.Label(left_panel, text="Conversas", font=("Helvetica", 12, "bold")).pack(
            pady=5
        )
        self.user_list = tk.Listbox(
            left_panel, width=25, font=("Helvetica", 11), height=15
        )
        self.user_list.pack(side=tk.TOP, fill=tk.Y, expand=True)
        self.user_list.bind("<<ListboxSelect>>", self.on_select_user)
        self.create_group_button = tk.Button(
            left_panel,
            text="Criar Novo Grupo",
            font=("Helvetica", 10),
            command=self.open_create_group_dialog,
        )
        self.create_group_button.pack(side=tk.BOTTOM, fill=tk.X, pady=10, padx=5)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)

        chat_frame = tk.Frame(self.main_frame)
        self.chat_area = scrolledtext.ScrolledText(
            chat_frame, wrap=tk.WORD, state="disabled", font=("Helvetica", 12)
        )
        self.chat_area.pack(expand=True, fill=tk.BOTH)

        msg_frame = tk.Frame(chat_frame)
        self.msg_entry = tk.Entry(msg_frame, font=("Helvetica", 12))
        self.msg_entry.pack(
            side=tk.LEFT, expand=True, fill=tk.X, ipady=8, pady=5, padx=5
        )
        self.msg_entry.bind("<Return>", self.send_message)
        self.send_button = tk.Button(
            msg_frame, text="Enviar", font=("Helvetica", 11), command=self.send_message
        )
        self.send_button.pack(side=tk.RIGHT, pady=5, padx=5)
        msg_frame.pack(side=tk.BOTTOM, fill=tk.X)
        chat_frame.pack(side=tk.RIGHT, expand=True, fill=tk.BOTH, pady=10, padx=5)

    def connect(self):
        client_id = self.id_entry.get().strip()
        if not client_id:
            messagebox.showerror("Erro", "Por favor, insira um ID.")
            return

        server_host = "localhost"
        server_port = 4433
        cacert = "cert.pem"

        self.logic = ChatLogic(server_host, server_port, cacert, client_id)
        self.logic.on_new_message = self.display_message
        self.logic.on_update_ui = self.update_user_list

        threading.Thread(target=self.run_asyncio_loop, daemon=True).start()

    def run_asyncio_loop(self):
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.async_tasks())
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Erro de Conexão", str(e)))

    async def async_tasks(self):
        if await self.logic.publish_key():
            self.root.after(0, self.show_main_chat)
            self.update_user_list()
            await self.logic.poll_blobs()
        else:
            self.root.after(
                0,
                lambda: messagebox.showerror(
                    "Erro", "Falha ao publicar a chave no servidor."
                ),
            )

    def show_main_chat(self):
        self.login_frame.pack_forget()
        self.main_frame.pack(expand=True, fill=tk.BOTH)
        self.root.title(f"Chat Seguro - {self.logic.client_id}")

    def on_select_user(self, event):
        selection = event.widget.curselection()
        if not selection:
            return

        index = selection[0]
        self.current_chat = event.widget.get(index).split(" ")[0]
        self.chat_area.config(state="normal")
        self.chat_area.delete(1.0, tk.END)

        if self.current_chat in self.logic.conversations:
            history = self.logic.conversations[self.current_chat].get("history", [])
            for ts, sender, msg in history:
                display_sender = "Você" if sender == self.logic.client_id else sender
                self.chat_area.insert(tk.END, f"[{ts}] {display_sender}: {msg}\n")

        self.chat_area.yview(tk.END)
        self.chat_area.config(state="disabled")
        self.root.title(f"Chat com {self.current_chat} - {self.logic.client_id}")

    def update_user_list(self):
        if not self.loop:
            return
        asyncio.run_coroutine_threadsafe(self.update_user_list_async(), self.loop)

    async def update_user_list_async(self):
        await self.logic.list_all()

        def update_gui():
            current_selection = self.user_list.curselection()
            self.user_list.delete(0, tk.END)
            sorted_convs = sorted(
                self.logic.conversations.items(),
                key=lambda item: (item[1]["type"] != "group", item[0]),
            )
            for i, (conv, data) in enumerate(sorted_convs):
                conv_type = data.get("type", "private")
                self.user_list.insert(tk.END, f"{conv} ({conv_type})")
                if conv == self.current_chat and not current_selection:
                    self.user_list.selection_set(i)

        if self.loop.is_running():
            self.root.after(0, update_gui)

    def open_create_group_dialog(self):
        async def fetch_and_open():
            clients, _ = await self.logic.list_all()

            def open_dialog():
                other_clients = [c for c in clients if c != self.logic.client_id]
                dialog = CreateGroupDialog(self.root, "Criar Novo Grupo", other_clients)
                if dialog.result:
                    group_name, members = dialog.result
                    asyncio.run_coroutine_threadsafe(
                        self.logic.create_group(group_name, members), self.loop
                    )

            self.root.after(0, open_dialog)

        asyncio.run_coroutine_threadsafe(fetch_and_open(), self.loop)

    # lógica de envio de mensagens
    def send_message(self, event=None):
        if not self.logic or not self.current_chat:
            return

        message = self.msg_entry.get()
        if not message:
            return

        ts = time.strftime("%H:%M:%S")
        self.logic.conversations[self.current_chat]["history"].append(
            (ts, self.logic.client_id, message)
        )
        self.display_message(self.current_chat, f"[{ts}] Você: {message}")

        conv_type = self.logic.conversations[self.current_chat]["type"]

        if conv_type == "private":
            coro = self.logic.send_private_message(self.current_chat, message)
        elif conv_type == "group":
            coro = self.logic.send_group_message(self.current_chat, message)
        else:
            return

        asyncio.run_coroutine_threadsafe(coro, self.loop)
        self.msg_entry.delete(0, tk.END)

    def display_message(self, peer, message):
        def update_gui():
            if peer == self.current_chat:
                self.chat_area.config(state="normal")
                self.chat_area.insert(tk.END, message + "\n")
                self.chat_area.yview(tk.END)
                self.chat_area.config(state="disabled")

        if self.loop and self.loop.is_running():
            self.root.after(0, update_gui)


if __name__ == "__main__":
    root = tk.Tk()
    app = ChatGUI(root)
    root.mainloop()

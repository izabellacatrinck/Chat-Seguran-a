import asyncio
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext

from chat_client_logic import ChatLogic


class ChatGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Chat Seguro")
        self.logic = None
        self.current_chat = None # Para saber com quem estamos conversando

        # --- Tela de Login ---
        self.login_frame = tk.Frame(root)
        tk.Label(self.login_frame, text="Seu ID:").pack(padx=10, pady=5)
        self.id_entry = tk.Entry(self.login_frame)
        self.id_entry.pack(padx=10, pady=5)
        tk.Button(self.login_frame, text="Conectar", command=self.connect).pack(pady=10)
        self.login_frame.pack(padx=20, pady=20)

        # --- Tela Principal do Chat (inicialmente oculta) ---
        self.main_frame = tk.Frame(root)
        # Lista de usuários/conversas
        self.user_list = tk.Listbox(self.main_frame, width=25)
        self.user_list.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        self.user_list.bind('<<ListboxSelect>>', self.on_select_user)

        # Área da conversa
        chat_frame = tk.Frame(self.main_frame)
        self.chat_area = scrolledtext.ScrolledText(chat_frame, wrap=tk.WORD, state='disabled')
        self.chat_area.pack(expand=True, fill=tk.BOTH)
        # Área de digitação
        self.msg_entry = tk.Entry(chat_frame, width=60)
        self.msg_entry.pack(side=tk.LEFT, expand=True, fill=tk.X, ipady=5, pady=5, padx=5)
        self.msg_entry.bind("<Return>", self.send_message)
        self.send_button = tk.Button(chat_frame, text="Enviar", command=self.send_message)
        self.send_button.pack(side=tk.RIGHT, pady=5, padx=5)
        chat_frame.pack(side=tk.RIGHT, expand=True, fill=tk.BOTH)

    def connect(self):
        client_id = self.id_entry.get()
        if not client_id:
            messagebox.showerror("Erro", "Por favor, insira um ID.")
            return

        # Parâmetros de conexão (ajuste se necessário)
        server_host = "localhost"
        server_port = 4433
        cacert = "cert.pem"

        # Inicializa a lógica do chat
        self.logic = ChatLogic(server_host, server_port, cacert, client_id)
        self.logic.on_new_message = self.display_message # Configura o callback

        # O asyncio precisa rodar em uma thread separada para não bloquear a GUI
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
            # Se a chave foi publicada, esconde o login e mostra o chat
            self.root.after(0, self.show_main_chat)
            # Inicia o polling de mensagens
            asyncio.create_task(self.logic.poll_blobs())
            # Carrega a lista de usuários
            await self.update_user_list()
        else:
            self.root.after(0, lambda: messagebox.showerror("Erro", "Falha ao publicar a chave no servidor."))

    def show_main_chat(self):
        self.login_frame.pack_forget()
        self.main_frame.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)
        self.root.title(f"Chat Seguro - {self.logic.client_id}")

    def on_select_user(self, event):
        selection = event.widget.curselection()
        if selection:
            index = selection[0]
            self.current_chat = event.widget.get(index)
            self.chat_area.config(state='normal')
            self.chat_area.delete(1.0, tk.END)
            # Aqui você carregaria o histórico da conversa, se o tivesse salvo
            self.chat_area.config(state='disabled')
            self.root.title(f"Chat com {self.current_chat} - {self.logic.client_id}")


    async def update_user_list(self):
        clients, _ = await self.logic.list_all()
        # A atualização da GUI deve ser feita na thread principal
        def update_gui():
            self.user_list.delete(0, tk.END)
            for client in clients:
                self.user_list.insert(tk.END, client)
        self.root.after(0, update_gui)


    def send_message(self, event=None):
        message = self.msg_entry.get()
        if message and self.current_chat:
            # Envia a mensagem usando a lógica async
            asyncio.run_coroutine_threadsafe(
                self.logic.send_private_message(self.current_chat, message),
                self.loop
            )
            # Mostra a mensagem enviada na tela
            self.display_message(self.current_chat, f"[{time.strftime('%H:%M:%S')}] Você: {message}")
            self.msg_entry.delete(0, tk.END)

    def display_message(self, peer, message):
        # A atualização da GUI deve ser feita na thread principal
        def update_gui():
            # Apenas mostra a mensagem se a conversa com o peer estiver aberta
            if peer == self.current_chat:
                self.chat_area.config(state='normal')
                self.chat_area.insert(tk.END, message + "\n")
                self.chat_area.yview(tk.END) # Rola para o final
                self.chat_area.config(state='disabled')
        self.root.after(0, update_gui)


if __name__ == "__main__":
    root = tk.Tk()
    app = ChatGUI(root)
    root.mainloop()
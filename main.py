import time
import argparse
import sys
from solana.rpc.api import Client
from solana.transaction import Transaction
from solana.keypair import Keypair
from solana.publickey import PublicKey
from solders.instruction import Instruction
from base58 import b58encode, b58decode
import json
from typing import Optional, Dict, List
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import get_associated_token_address, create_associated_token_account
import struct
from solana.system_program import TransferParams, transfer
from solana.transaction import AccountMeta, TransactionInstruction

RESET_COLOR = "\033[0m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"

SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"
solana_client = Client(SOLANA_RPC_URL)

RAYDIUM_SWAP_PROGRAM_ID = PublicKey("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8")
RAYDIUM_AMM_PROGRAM_ID = PublicKey("5quBtoiQqxF9Jv6KYKctB59NT3gtJD2Y65kdnB1Uev3h")

class SolanaTrader:
    def __init__(self, private_key: Optional[str] = None, seed_phrase: Optional[str] = None):
        if private_key:
            self.keypair = Keypair.from_secret_key(b58decode(private_key))
        elif seed_phrase:
            from mnemonic import Mnemonic
            mnemo = Mnemonic("english")
            seed = mnemo.to_seed(seed_phrase)
            self.keypair = Keypair.from_seed(seed[:32])
        else:
            raise ValueError("Either private key or seed phrase must be provided")
        self.public_key = str(self.keypair.public_key)

    def get_token_account(self, token_mint: str) -> str:
        token_mint_pubkey = PublicKey(token_mint)
        owner_pubkey = PublicKey(self.public_key)
        ata = get_associated_token_address(owner_pubkey, token_mint_pubkey)
        account_info = solana_client.get_account_info(ata)
        if not account_info.value:
            create_ata_ix = create_associated_token_account(payer=owner_pubkey,owner=owner_pubkey,mint=token_mint_pubkey)
            transaction = Transaction().add(create_ata_ix)
            self._sign_and_send_transaction(transaction)
        return str(ata)

    def swap_exact_tokens_for_tokens(self,amm_id: str,amount_in: int,min_amount_out: int,token_mint_in: str,token_mint_out: str):
        try:
            token_account_in = PublicKey(self.get_token_account(token_mint_in))
            token_account_out = PublicKey(self.get_token_account(token_mint_out))
            amm_authority = PublicKey.find_program_address([bytes(amm_id, 'utf-8')],RAYDIUM_AMM_PROGRAM_ID)[0]
            swap_instruction_data = struct.pack('<BQQQ',2,amount_in,min_amount_out,0,)
            keys = [
                AccountMeta(PublicKey(amm_id), False, True),
                AccountMeta(amm_authority, False, False),
                AccountMeta(PublicKey(self.public_key), True, False),
                AccountMeta(token_account_in, False, True),
                AccountMeta(token_account_out, False, True),
                AccountMeta(PublicKey(token_mint_in), False, False),
                AccountMeta(PublicKey(token_mint_out), False, False),
                AccountMeta(TOKEN_PROGRAM_ID, False, False),
            ]
            swap_ix = TransactionInstruction(keys=keys,program_id=RAYDIUM_SWAP_PROGRAM_ID,data=swap_instruction_data)
            transaction = Transaction().add(swap_ix)
            signature = self._sign_and_send_transaction(transaction)
            print(f"{GREEN}Swap transaction sent: {signature}{RESET_COLOR}")
            return signature
        except Exception as e:
            print(f"{RED}Error executing swap: {str(e)}{RESET_COLOR}")
            return None

    def buy_token_with_sol(self, token_mint: str, sol_amount: float, max_slippage: float = 0.01):
        lamports = int(sol_amount * 1e9)
        try:
            wsol_mint = "So11111111111111111111111111111111111111112"
            wsol_account = self.get_token_account(wsol_mint)
            wrap_sol_ix = transfer(TransferParams(from_pubkey=PublicKey(self.public_key),to_pubkey=PublicKey(wsol_account),lamports=lamports))
            min_amount_out = int(lamports * (1 - max_slippage))
            signature = self.swap_exact_tokens_for_tokens("POOL_ID",lamports,min_amount_out,wsol_mint,token_mint)
            return signature
        except Exception as e:
            print(f"{RED}Error buying token: {str(e)}{RESET_COLOR}")
            return None

    def sell_token_for_sol(self, token_mint: str, amount: float, max_slippage: float = 0.01):
        try:
            token_account = self.get_token_account(token_mint)
            token_info = solana_client.get_token_account_balance(token_account)
            decimals = token_info.value.decimals
            amount_in = int(amount * (10 ** decimals))
            min_amount_out = int(amount_in * (1 - max_slippage))
            wsol_mint = "So11111111111111111111111111111111111111112"
            signature = self.swap_exact_tokens_for_tokens("POOL_ID",amount_in,min_amount_out,token_mint,wsol_mint)
            return signature
        except Exception as e:
            print(f"{RED}Error selling token: {str(e)}{RESET_COLOR}")
            return None

    def get_token_balance(self, token_mint: str) -> float:
        try:
            token_account = self.get_token_account(token_mint)
            balance = solana_client.get_token_account_balance(token_account)
            return float(balance.value.amount) / (10 ** balance.value.decimals)
        except Exception as e:
            print(f"{RED}Error getting token balance: {str(e)}{RESET_COLOR}")
            return 0.0

    def get_sol_balance(self) -> float:
        try:
            balance = solana_client.get_balance(self.public_key)
            return float(balance.value) / 1e9
        except Exception as e:
            print(f"{RED}Error getting SOL balance: {str(e)}{RESET_COLOR}")
            return 0.0

    def _sign_and_send_transaction(self, transaction: Transaction) -> str:
        transaction.sign(self.keypair)
        result = solana_client.send_transaction(transaction)
        return result.value

    def monitor_token_with_trading(self, token_mint: str, initial_price: float = None, interval: int = 15, profit_target: float = 50.0, stop_loss: float = -10.0):
        print(f"\nMonitoring token {token_mint} with auto-trading...")
        if initial_price is None:
            initial_price = self.get_token_balance(token_mint)
        while True:
            try:
                current_price = self.get_token_balance(token_mint)
                if current_price > 0:
                    percent_change = ((current_price - initial_price) / initial_price) * 100
                    if percent_change > 0:
                        color = GREEN
                    elif percent_change < 0:
                        color = RED
                    else:
                        color = YELLOW
                    print(f"Token balance: {current_price:.6f}, Change: {color}{percent_change:.2f}%{RESET_COLOR}")
                    if percent_change >= profit_target:
                        print(f"{GREEN}Profit target reached! Selling...{RESET_COLOR}")
                        self.sell_token_for_sol(token_mint, current_price)
                        break
                    elif percent_change <= stop_loss:
                        print(f"{RED}Stop loss triggered! Selling...{RESET_COLOR}")
                        self.sell_token_for_sol(token_mint, current_price)
                        break
                time.sleep(interval)
            except Exception as e:
                print(f"{RED}Error monitoring token: {str(e)}{RESET_COLOR}")
                time.sleep(interval)

def parse_arguments():
    parser = argparse.ArgumentParser(description="Solana Token Trader")
    parser.add_argument("--private-key", type=str, help="Private key to the wallet")
    parser.add_argument("--seed-phrase", type=str, help="Seed phrase to the wallet")
    parser.add_argument("--token-mint", type=str, help="Token mint address to trade")
    parser.add_argument("--action", type=str, choices=['buy', 'sell', 'monitor'], required=True)
    parser.add_argument("--amount", type=float, help="Amount of SOL to spend or tokens to sell")
    parser.add_argument("--profit-target", type=float, default=50.0, help="Profit target percentage")
    parser.add_argument("--stop-loss", type=float, default=-10.0, help="Stop loss percentage")
    parser.add_argument("--interval", type=int, default=15, help="Monitoring interval in seconds")
    args = parser.parse_args()
    if not args.private_key and not args.seed_phrase:
        parser.error("You must provide either --private-key or --seed-phrase")
    if not args.token_mint:
        parser.error("You must provide --token-mint")
    if args.action in ['buy', 'sell'] and not args.amount:
        parser.error("You must provide --amount for buy/sell actions")
    return args

def main():
    args = parse_arguments()
    trader = SolanaTrader(private_key=args.private_key,seed_phrase=args.seed_phrase)
    sol_balance = trader.get_sol_balance()
    token_balance = trader.get_token_balance(args.token_mint)
    print(f"\nInitial SOL balance: {sol_balance:.6f}")
    print(f"Initial token balance: {token_balance:.6f}")
    if args.action == 'buy':
        trader.buy_token_with_sol(args.token_mint, args.amount)
    elif args.action == 'sell':
        trader.sell_token_for_sol(args.token_mint, args.amount)
    elif args.action == 'monitor':
        trader.monitor_token_with_trading(args.token_mint,token_balance,args.interval,args.profit_target,args.stop_loss)

if __name__ == "__main__":
    main()